/*
 * fb_tux.c — Draw Tux (Linux penguin) on /dev/fb0
 *
 * Usage:
 *   fb_tux              — Draw official Linux boot logo centered
 *   fb_tux logo [N]     — Draw N official boot logos (like kernel boot)
 *   fb_tux tux          — Draw hi-res vector Tux
 *   fb_tux color        — Draw colorful test pattern
 *   fb_tux gradient     — Draw RGB gradient
 *   fb_tux clear        — Clear framebuffer to black
 *   fb_tux fill RRGGBB  — Fill with color (hex)
 *   fb_tux text         — Restore fbcon text mode
 *
 * Framebuffer: 640×480, XRGB8888 (32bpp, little-endian)
 *
 * NOTE: Switches /dev/tty0 to KD_GRAPHICS mode to suppress fbcon
 *       text overlay.  Use "fb_tux text" to restore.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <linux/fb.h>
#include <linux/kd.h>
#include <linux/vt.h>

#include "logo_tux_data.h"

#define FB_WIDTH   640
#define FB_HEIGHT  480
#define FB_BPP     4  /* bytes per pixel (XRGB8888) */
#define FB_SIZE    (FB_WIDTH * FB_HEIGHT * FB_BPP)

/* ── Framebuffer file descriptor (global for flush) ───────── */
static int fb_fd = -1;

/* ── Suppress / restore fbcon text overlay ────────────────── */
static int tty_fd = -1;

static void fbcon_disable(void)
{
    tty_fd = open("/dev/tty0", O_RDWR);
    if (tty_fd < 0) {
        /* try /dev/console as fallback */
        tty_fd = open("/dev/console", O_RDWR);
    }
    if (tty_fd >= 0) {
        ioctl(tty_fd, KDSETMODE, KD_GRAPHICS);   /* stop fbcon rendering */
    }
}

static void fbcon_restore(void)
{
    if (tty_fd >= 0) {
        ioctl(tty_fd, KDSETMODE, KD_TEXT);        /* resume fbcon */
        close(tty_fd);
        tty_fd = -1;
    }
}

/* ── Force framebuffer flush to GPU scanout ───────────────── */
/*
 * VirtIO-GPU: kernel mmap'd buffer (GEM object) lives in system RAM,
 * NOT at the scanout address. When fbcon is active, it triggers
 * RESOURCE_FLUSH via the VirtIO virtqueue.
 *
 * Solution: Use standard fbdev flush ioctls to trigger the update.
 */
static unsigned char *fb_mmap_ptr = NULL;

static void fb_flush(void)
{
    if (fb_fd < 0 || !fb_mmap_ptr || fb_mmap_ptr == MAP_FAILED)
        return;

    /* ── Standard fbdev flush ioctls ─────────── */
    msync(fb_mmap_ptr, FB_SIZE, MS_SYNC);
    fsync(fb_fd);

    struct fb_var_screeninfo vi;
    if (ioctl(fb_fd, FBIOGET_VSCREENINFO, &vi) == 0) {
        vi.xoffset = 0;
        vi.yoffset = 0;
        vi.activate = FB_ACTIVATE_NOW | FB_ACTIVATE_FORCE;
        ioctl(fb_fd, FBIOPAN_DISPLAY, &vi);
    }

    int dummy = 0;
    ioctl(fb_fd, FBIO_WAITFORVSYNC, &dummy);
}

/* ── Pixel drawing primitives ─────────────────────────────── */
static inline void put_pixel(unsigned char *fb, int x, int y, uint32_t color)
{
    if (x < 0 || x >= FB_WIDTH || y < 0 || y >= FB_HEIGHT)
        return;
    ((uint32_t *)fb)[(y * FB_WIDTH) + x] = color;
}

static void draw_rect(unsigned char *fb, int x, int y, int w, int h, uint32_t color)
{
    for (int dy = 0; dy < h; dy++)
        for (int dx = 0; dx < w; dx++)
            put_pixel(fb, x + dx, y + dy, color);
}

/* ── Alpha blending (Porter-Duff "over" operator) ──────────── */
static inline uint32_t blend_over(uint32_t src, uint32_t dst)
{
    uint32_t sa = (src >> 24) & 0xFF;
    uint32_t sr = (src >> 16) & 0xFF;
    uint32_t sg = (src >>  8) & 0xFF;
    uint32_t sb = (src >>  0) & 0xFF;

    uint32_t da = (dst >> 24) & 0xFF;
    uint32_t dr = (dst >> 16) & 0xFF;
    uint32_t dg = (dst >>  8) & 0xFF;
    uint32_t db = (dst >>  0) & 0xFF;

    /* result = src + dst * (1 - src_alpha) */
    uint32_t inv_sa = 255 - sa;
    uint32_t ra = sa + ((da * inv_sa) / 255);
    uint32_t rr = sr + ((dr * inv_sa) / 255);
    uint32_t rg = sg + ((dg * inv_sa) / 255);
    uint32_t rb = sb + ((db * inv_sa) / 255);

    return (ra << 24) | (rr << 16) | (rg << 8) | rb;
}

static void blit_rgba(unsigned char *fb, int dx, int dy,
                     const unsigned char *src, int sw, int sh)
{
    for (int y = 0; y < sh; y++) {
        for (int x = 0; x < sw; x++) {
            int sx_pos = (y * sw + x) * 4;
            uint32_t src_px = ((uint32_t)src[sx_pos + 3] << 24) |  /* A */
                             ((uint32_t)src[sx_pos + 0] << 16) |  /* R */
                             ((uint32_t)src[sx_pos + 1] <<  8) |  /* G */
                             ((uint32_t)src[sx_pos + 2] <<  0);   /* B */

            int fx = dx + x;
            int fy = dy + y;
            if (fx >= 0 && fx < FB_WIDTH && fy >= 0 && fy < FB_HEIGHT) {
                uint32_t *dst_ptr = ((uint32_t *)fb) + (fy * FB_WIDTH + fx);
                *dst_ptr = blend_over(src_px, *dst_ptr);
            }
        }
    }
}

/* ── Text rendering (8x16 embedded font) ───────────────────── */
static const unsigned char font_8x16[256][16] = {
    /* Simple embedded 8x16 ASCII font - only printable chars */
    [' '] = {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
             0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00},
    ['A'] = {0x00,0x00,0x10,0x28,0x28,0x44,0x44,0x7C,
             0x44,0x82,0x82,0x82,0x00,0x00,0x00,0x00},
    /* ... (truncated for brevity - full font would be here) ... */
};

static void draw_char(unsigned char *fb, int x, int y, char c, uint32_t fg, uint32_t bg)
{
    const unsigned char *glyph = font_8x16[(unsigned char)c];
    for (int row = 0; row < 16; row++) {
        unsigned char bits = glyph[row];
        for (int col = 0; col < 8; col++) {
            uint32_t color = (bits & (0x80 >> col)) ? fg : bg;
            put_pixel(fb, x + col, y + row, color);
        }
    }
}

static void draw_string(unsigned char *fb, int x, int y, const char *str,
                       uint32_t fg, uint32_t bg)
{
    while (*str) {
        draw_char(fb, x, y, *str, fg, bg);
        x += 8;
        str++;
    }
}

/* ── Draw test patterns ───────────────────────────────────── */
static void draw_gradient_rgb(unsigned char *fb)
{
    for (int y = 0; y < FB_HEIGHT; y++) {
        for (int x = 0; x < FB_WIDTH; x++) {
            uint8_t r = (x * 255) / FB_WIDTH;
            uint8_t g = (y * 255) / FB_HEIGHT;
            uint8_t b = ((x + y) * 127) / (FB_WIDTH + FB_HEIGHT);
            put_pixel(fb, x, y, 0xFF000000 | (r << 16) | (g << 8) | b);
        }
    }
}

static void draw_color_bars(unsigned char *fb)
{
    uint32_t colors[8] = {
        0xFFFFFFFF, 0xFFFFFF00, 0xFF00FFFF, 0xFF00FF00,
        0xFFFF00FF, 0xFFFF0000, 0xFF0000FF, 0xFF000000
    };
    int bar_w = FB_WIDTH / 8;
    for (int i = 0; i < 8; i++) {
        draw_rect(fb, i * bar_w, 0, bar_w, FB_HEIGHT, colors[i]);
    }
}

/* ── Official Linux boot logo (224x208, centered) ──────────── */
static void draw_official_logo(unsigned char *fb, int center_x, int center_y)
{
    int x = center_x - (logo_linux_clut224_width / 2);
    int y = center_y - (logo_linux_clut224_height / 2);
    
    /* The logo data is indexed color (CLUT), convert to ARGB */
    for (int py = 0; py < logo_linux_clut224_height; py++) {
        for (int px = 0; px < logo_linux_clut224_width; px++) {
            unsigned char idx = logo_linux_clut224_data[py * logo_linux_clut224_width + px];
            unsigned char r = logo_linux_clut224_clut[idx * 3 + 0];
            unsigned char g = logo_linux_clut224_clut[idx * 3 + 1];
            unsigned char b = logo_linux_clut224_clut[idx * 3 + 2];
            uint32_t color = 0xFF000000 | (r << 16) | (g << 8) | b;
            put_pixel(fb, x + px, y + py, color);
        }
    }
}

/* ── Draw multiple boot logos (like kernel boot) ───────────── */
static void draw_multiple_logos(unsigned char *fb, int count)
{
    /* Clear to black first */
    memset(fb, 0, FB_SIZE);
    
    int logo_w = logo_linux_clut224_width;
    int logo_h = logo_linux_clut224_height;
    int margin = 20;
    int cols = 4;  /* logos per row */
    
    int start_x = (FB_WIDTH - (logo_w * cols + margin * (cols - 1))) / 2;
    int start_y = margin;
    
    for (int i = 0; i < count; i++) {
        int row = i / cols;
        int col = i % cols;
        int x = start_x + col * (logo_w + margin);
        int y = start_y + row * (logo_h + margin);
        
        if (y + logo_h > FB_HEIGHT)
            break;
            
        draw_official_logo(fb, x + logo_w/2, y + logo_h/2);
    }
}

/* ── High-resolution vector Tux rendering ──────────────────── */
static void draw_vector_tux(unsigned char *fb)
{
    int cx = FB_WIDTH / 2;
    int cy = FB_HEIGHT / 2;
    
    /* Body (black ellipse) */
    for (int y = -80; y <= 80; y++) {
        for (int x = -50; x <= 50; x++) {
            if (x*x/2500.0 + y*y/6400.0 <= 1.0)
                put_pixel(fb, cx + x, cy + y, 0xFF000000);
        }
    }
    
    /* Belly (white ellipse) */
    for (int y = -40; y <= 60; y++) {
        for (int x = -30; x <= 30; x++) {
            if (x*x/900.0 + y*y/2500.0 <= 1.0)
                put_pixel(fb, cx + x, cy + y + 10, 0xFFFFFFFF);
        }
    }
    
    /* Eyes (white circles with black pupils) */
    for (int dy = -8; dy <= 8; dy++) {
        for (int dx = -8; dx <= 8; dx++) {
            if (dx*dx + dy*dy <= 64) {
                put_pixel(fb, cx - 20 + dx, cy - 30 + dy, 0xFFFFFFFF);
                put_pixel(fb, cx + 20 + dx, cy - 30 + dy, 0xFFFFFFFF);
            }
            if (dx*dx + dy*dy <= 16) {
                put_pixel(fb, cx - 20 + dx, cy - 28 + dy, 0xFF000000);
                put_pixel(fb, cx + 20 + dx, cy - 28 + dy, 0xFF000000);
            }
        }
    }
    
    /* Beak (yellow/orange) */
    for (int y = 0; y < 10; y++) {
        for (int x = -8 + y/2; x <= 8 - y/2; x++) {
            put_pixel(fb, cx + x, cy - 10 + y, 0xFFFFA500);
        }
    }
    
    /* Feet (yellow) */
    for (int foot = 0; foot < 2; foot++) {
        int foot_x = cx + (foot == 0 ? -25 : 25);
        int foot_y = cy + 75;
        
        /* Foot pad */
        for (int y = 0; y < 15; y++) {
            for (int x = -12; x <= 12; x++) {
                put_pixel(fb, foot_x + x, foot_y + y, 0xFFFFA500);
            }
        }
        
        /* Three toes */
        for (int toe = 0; toe < 3; toe++) {
            int toe_x = foot_x + (toe - 1) * 10;
            for (int y = 0; y < 20; y++) {
                for (int x = -3; x <= 3; x++) {
                    put_pixel(fb, toe_x + x, foot_y + 15 + y, 0xFFFFA500);
                }
            }
        }
    }
    
    /* Wings/flippers */
    for (int wing = 0; wing < 2; wing++) {
        int wing_x = cx + (wing == 0 ? -50 : 50);
        int dir = (wing == 0 ? -1 : 1);
        
        for (int y = -30; y <= 30; y++) {
            for (int x = 0; x < 25; x++) {
                if (y*y/900.0 + x*x/625.0 <= 1.0)
                    put_pixel(fb, wing_x + dir * x, cy + y, 0xFF000000);
            }
        }
    }
}

/* ── Main ─────────────────────────────────────────────────── */
int main(int argc, char *argv[])
{
    const char *mode = (argc > 1) ? argv[1] : "logo";
    
    if (strcmp(mode, "text") == 0) {
        fbcon_restore();
        printf("Restored text mode\n");
        return 0;
    }
    
    /* Open framebuffer */
    fb_fd = open("/dev/fb0", O_RDWR);
    if (fb_fd < 0) {
        perror("/dev/fb0");
        return 1;
    }
    
    /* Verify resolution */
    struct fb_var_screeninfo vinfo;
    if (ioctl(fb_fd, FBIOGET_VSCREENINFO, &vinfo) == 0) {
        printf("Framebuffer: %dx%d, %d bpp\n",
               vinfo.xres, vinfo.yres, vinfo.bits_per_pixel);
    }
    struct fb_fix_screeninfo finfo;
    if (ioctl(fb_fd, FBIOGET_FSCREENINFO, &finfo) == 0) {
        printf("  Type: %d, Line length: %d\n",
               finfo.type, finfo.line_length);
    }
    
    /* Map framebuffer */
    unsigned char *fb = mmap(NULL, FB_SIZE, PROT_READ | PROT_WRITE,
                            MAP_SHARED, fb_fd, 0);
    if (fb == MAP_FAILED) {
        perror("mmap");
        close(fb_fd);
        return 1;
    }
    
    fb_mmap_ptr = fb;  /* store for fb_flush() msync */
    
    /* Suppress fbcon text overlay */
    fbcon_disable();
    
    /* Render based on mode */
    if (strcmp(mode, "logo") == 0) {
        int count = (argc > 2) ? atoi(argv[2]) : 1;
        if (count == 1) {
            memset(fb, 0, FB_SIZE);
            draw_official_logo(fb, FB_WIDTH/2, FB_HEIGHT/2);
            printf("Drew official Linux boot logo (centered)\n");
        } else {
            draw_multiple_logos(fb, count);
            printf("Drew %d Linux boot logos\n", count);
        }
    }
    else if (strcmp(mode, "tux") == 0) {
        memset(fb, 0x40, FB_SIZE);  /* dark gray background */
        draw_vector_tux(fb);
        printf("Drew hi-res vector Tux\n");
    }
    else if (strcmp(mode, "color") == 0) {
        draw_color_bars(fb);
        printf("Drew color bars\n");
    }
    else if (strcmp(mode, "gradient") == 0) {
        draw_gradient_rgb(fb);
        printf("Drew RGB gradient\n");
    }
    else if (strcmp(mode, "clear") == 0) {
        memset(fb, 0, FB_SIZE);
        printf("Cleared framebuffer\n");
    }
    else if (strcmp(mode, "fill") == 0) {
        uint32_t color = 0xFF000000;
        if (argc > 2)
            color = 0xFF000000 | (uint32_t)strtol(argv[2], NULL, 16);
        for (int i = 0; i < FB_WIDTH * FB_HEIGHT; i++)
            ((uint32_t *)fb)[i] = color;
        printf("Filled with color 0x%08X\n", color);
    }
    else {
        fprintf(stderr, "Unknown mode: %s\n", mode);
        fprintf(stderr, "Usage: fb_tux [logo|tux|color|gradient|clear|fill|text]\n");
        munmap(fb, FB_SIZE);
        close(fb_fd);
        return 1;
    }
    
    /* Flush to display */
    fb_flush();
    
    /* Cleanup */
    munmap(fb, FB_SIZE);
    close(fb_fd);
    fb_fd = -1;
    
    printf("Hint: Use 'fb_tux text' to restore text mode\n");
    return 0;
}
