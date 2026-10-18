// doomgeneric port for the Snapmaker U1 display.
//
// The U1 shows its UI with LVGL on /dev/fb0 (480 x 320). With the
// Snapmaker app stopped (`lmd_cli -K gui`, see run.sh) this port owns the
// framebuffer: Doom renders 320 x 200, we scale it by 1.5 to 480 x 300 and
// center it. SDL2 only handles game controllers here (and sound, in
// i_sdlsound.c); keyboards come straight from evdev, and a tap on the
// touchscreen quits.
//
// Based on doomgeneric_linuxvt.c (framebuffer setup) from doomgeneric.

#include <SDL.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "doomgeneric.h"
#include "i_system.h"

// Doom key codes from doomkeys.h; that header clashes with
// linux/input.h (KEY_ENTER, KEY_F1, ...), so the values are copied here.
#define DK_RIGHTARROW 0xae
#define DK_LEFTARROW 0xac
#define DK_UPARROW 0xad
#define DK_DOWNARROW 0xaf
#define DK_STRAFE_L 0xa0
#define DK_STRAFE_R 0xa1
#define DK_USE 0xa2
#define DK_FIRE 0xa3
#define DK_ESCAPE 27
#define DK_ENTER 13
#define DK_TAB 9
#define DK_BACKSPACE 0x7f
#define DK_RSHIFT (0x80 + 0x36)
#define DK_RALT (0x80 + 0x38)

#define SCALED_W (DOOMGENERIC_RESX * 3 / 2)
#define SCALED_H (DOOMGENERIC_RESY * 3 / 2)
#define MAX_INPUTS 16
#define KEY_QUEUE 64
#define STICK_DEADZONE 12000   // of 32767
#define TRIGGER_PRESSED 16000

static uint8_t *fb;            // first byte of the visible page
static uint8_t *fb_map;
static size_t fb_map_size;
static struct fb_var_screeninfo var;
static unsigned int stride, bytes_pp, origin_x, origin_y;
static int src_x[SCALED_W], src_y[SCALED_H];

static int inputs[MAX_INPUTS];
static int is_touchscreen[MAX_INPUTS];
static int input_count;
static int axis_state[SDL_CONTROLLER_AXIS_MAX];   // -1, 0, 1
static int weapon = 1;
static unsigned short key_queue[KEY_QUEUE];
static unsigned int key_read, key_write;
static struct timespec start;

static unsigned char doom_key(unsigned int code)
{
    static const char row1[] = "1234567890";
    static const char row2[] = "qwertyuiop";
    static const char row3[] = "asdfghjkl";
    static const char row4[] = "zxcvbnm";

    if (code >= KEY_1 && code <= KEY_0)
        return row1[code - KEY_1];
    if (code >= KEY_Q && code <= KEY_P)
        return row2[code - KEY_Q];
    if (code >= KEY_A && code <= KEY_L)
        return row3[code - KEY_A];
    if (code >= KEY_Z && code <= KEY_M)
        return row4[code - KEY_Z];
    switch (code) {
    case KEY_UP: return DK_UPARROW;
    case KEY_DOWN: return DK_DOWNARROW;
    case KEY_LEFT: return DK_LEFTARROW;
    case KEY_RIGHT: return DK_RIGHTARROW;
    case KEY_LEFTCTRL: case KEY_RIGHTCTRL: return DK_FIRE;
    case KEY_SPACE: return DK_USE;
    case KEY_LEFTSHIFT: case KEY_RIGHTSHIFT: return DK_RSHIFT;
    case KEY_LEFTALT: case KEY_RIGHTALT: return DK_RALT;
    case KEY_COMMA: return DK_STRAFE_L;
    case KEY_DOT: return DK_STRAFE_R;
    case KEY_ESC: return DK_ESCAPE;
    case KEY_ENTER: case KEY_KPENTER: return DK_ENTER;
    case KEY_TAB: return DK_TAB;
    case KEY_BACKSPACE: return DK_BACKSPACE;
    case KEY_MINUS: return '-';
    case KEY_EQUAL: return '=';
    }
    return 0;
}

static void queue_key(int pressed, unsigned char key)
{
    key_queue[key_write] = (unsigned short)(pressed << 8 | key);
    key_write = (key_write + 1) % KEY_QUEUE;
}

static void clear_screen(void)
{
    if (fb_map)
        memset(fb_map, 0, fb_map_size);
}

// A stick or trigger acts like two keys: crossing the threshold presses one,
// coming back releases it.
static void axis_keys(int axis, int value, int threshold, unsigned char neg, unsigned char pos)
{
    int now = value < -threshold ? -1 : value > threshold ? 1 : 0;

    if (now == axis_state[axis])
        return;
    if (axis_state[axis] < 0 && neg)
        queue_key(0, neg);
    if (axis_state[axis] > 0)
        queue_key(0, pos);
    if (now < 0 && neg)
        queue_key(1, neg);
    if (now > 0)
        queue_key(1, pos);
    axis_state[axis] = now;
}

static void controller_axis(const SDL_ControllerAxisEvent *e)
{
    switch (e->axis) {
    case SDL_CONTROLLER_AXIS_LEFTY:
        axis_keys(e->axis, e->value, STICK_DEADZONE, DK_UPARROW, DK_DOWNARROW);
        break;
    case SDL_CONTROLLER_AXIS_LEFTX:
        axis_keys(e->axis, e->value, STICK_DEADZONE, DK_STRAFE_L, DK_STRAFE_R);
        break;
    case SDL_CONTROLLER_AXIS_RIGHTX:
        axis_keys(e->axis, e->value, STICK_DEADZONE, DK_LEFTARROW, DK_RIGHTARROW);
        break;
    case SDL_CONTROLLER_AXIS_TRIGGERRIGHT:
        axis_keys(e->axis, e->value, TRIGGER_PRESSED, 0, DK_FIRE);
        if (e->value > TRIGGER_PRESSED) {
            SDL_GameController *pad = SDL_GameControllerFromInstanceID(e->which);
            SDL_GameControllerRumble(pad, 0x3000, 0x6000, 80);
        }
        break;
    case SDL_CONTROLLER_AXIS_TRIGGERLEFT:
        axis_keys(e->axis, e->value, TRIGGER_PRESSED, 0, DK_RSHIFT);
        break;
    }
}

static void controller_button(const SDL_ControllerButtonEvent *e)
{
    int down = e->state == SDL_PRESSED;

    switch (e->button) {
    case SDL_CONTROLLER_BUTTON_A:   // use in game, select in menus
        queue_key(down, DK_USE);
        queue_key(down, DK_ENTER);
        break;
    case SDL_CONTROLLER_BUTTON_B: queue_key(down, DK_BACKSPACE); break;
    case SDL_CONTROLLER_BUTTON_X: queue_key(down, DK_FIRE); break;
    case SDL_CONTROLLER_BUTTON_Y: queue_key(down, DK_TAB); break;
    case SDL_CONTROLLER_BUTTON_START: queue_key(down, DK_ESCAPE); break;
    case SDL_CONTROLLER_BUTTON_DPAD_UP: queue_key(down, DK_UPARROW); break;
    case SDL_CONTROLLER_BUTTON_DPAD_DOWN: queue_key(down, DK_DOWNARROW); break;
    case SDL_CONTROLLER_BUTTON_DPAD_LEFT: queue_key(down, DK_LEFTARROW); break;
    case SDL_CONTROLLER_BUTTON_DPAD_RIGHT: queue_key(down, DK_RIGHTARROW); break;
    case SDL_CONTROLLER_BUTTON_LEFTSHOULDER:
    case SDL_CONTROLLER_BUTTON_RIGHTSHOULDER:
        // Doom selects weapons by number; step through 1-7, the game skips
        // the ones the player does not have. The number key stays down as
        // long as the button: Doom only sees keys still held at the next tic.
        if (down)
            weapon = e->button == SDL_CONTROLLER_BUTTON_RIGHTSHOULDER ? weapon % 7 + 1
                                                                      : (weapon + 5) % 7 + 1;
        queue_key(down, '0' + weapon);
        break;
    }
}

static void read_controllers(void)
{
    SDL_Event e;

    while (SDL_PollEvent(&e)) {
        switch (e.type) {
        case SDL_CONTROLLERDEVICEADDED: {
            SDL_GameController *pad = SDL_GameControllerOpen(e.cdevice.which);
            if (pad) {
                printf("Controller: %s\n", SDL_GameControllerName(pad));
                SDL_GameControllerSetLED(pad, 0x00, 0x96, 0x88);   // OrcaOne teal
            }
            break;
        }
        case SDL_CONTROLLERAXISMOTION: controller_axis(&e.caxis); break;
        case SDL_CONTROLLERBUTTONDOWN:
        case SDL_CONTROLLERBUTTONUP: controller_button(&e.cbutton); break;
        case SDL_QUIT: I_Quit(); break;
        }
    }
}

static void read_inputs(void)
{
    struct input_event ev;

    for (int i = 0; i < input_count; i++) {
        while (read(inputs[i], &ev, sizeof ev) == sizeof ev) {
            if (is_touchscreen[i]) {
                // A tap quits. Some touch drivers only report a new tracking id.
                if ((ev.type == EV_KEY && ev.code == BTN_TOUCH && ev.value == 1)
                    || (ev.type == EV_ABS && ev.code == ABS_MT_TRACKING_ID && ev.value >= 0))
                    I_Quit();   // saves the config, exits; atexit clears the screen
                continue;
            }
            if (ev.type == EV_KEY && ev.value <= 1) {   // 2 is autorepeat
                unsigned char key = doom_key(ev.code);
                if (key)
                    queue_key(ev.value, key);
            }
        }
    }
    read_controllers();
}

static void open_inputs(void)
{
    DIR *dir = opendir("/dev/input");
    struct dirent *entry;

    if (!dir)
        I_Error("Cannot open /dev/input: %s", strerror(errno));
    while ((entry = readdir(dir)) && input_count < MAX_INPUTS) {
        char path[300];
        if (strncmp(entry->d_name, "event", 5) != 0)
            continue;
        snprintf(path, sizeof path, "/dev/input/%s", entry->d_name);
        int fd = open(path, O_RDONLY | O_NONBLOCK);
        if (fd < 0) {
            printf("Skipping %s: %s\n", path, strerror(errno));
            continue;
        }
        char name[128] = "?";
        unsigned long props = 0;
        ioctl(fd, EVIOCGNAME(sizeof name), name);
        ioctl(fd, EVIOCGPROP(sizeof props), &props);
        // Direct input = touchscreen; a controller's touchpad is not direct
        // and must not quit the game.
        is_touchscreen[input_count] = (props >> INPUT_PROP_DIRECT) & 1;
        printf("Input %s: %s%s\n", path, name, is_touchscreen[input_count] ? " (touchscreen)" : "");
        inputs[input_count++] = fd;
    }
    closedir(dir);
}

void DG_Init(void)
{
    struct fb_fix_screeninfo fix;
    int fd = open("/dev/fb0", O_RDWR);

    if (fd < 0)
        I_Error("Cannot open /dev/fb0: %s", strerror(errno));
    if (ioctl(fd, FBIOGET_VSCREENINFO, &var) || ioctl(fd, FBIOGET_FSCREENINFO, &fix))
        I_Error("Cannot read framebuffer info: %s", strerror(errno));

    stride = fix.line_length;
    bytes_pp = var.bits_per_pixel / 8;
    if (bytes_pp != 2 && bytes_pp != 4)
        I_Error("Unsupported framebuffer depth: %u bpp", var.bits_per_pixel);
    if (var.xres < SCALED_W || var.yres < SCALED_H)
        I_Error("Framebuffer %ux%u is smaller than %ux%u", var.xres, var.yres, SCALED_W, SCALED_H);
    printf("Framebuffer: %ux%u (virtual %ux%u, offset %u,%u), %u bpp, "
           "RGB offsets %u/%u/%u, line %u bytes\n",
           var.xres, var.yres, var.xres_virtual, var.yres_virtual, var.xoffset, var.yoffset,
           var.bits_per_pixel, var.red.offset, var.green.offset, var.blue.offset, stride);

    fb_map_size = (size_t)stride * var.yres_virtual;
    fb_map = mmap(NULL, fb_map_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (fb_map == MAP_FAILED)
        I_Error("Cannot map /dev/fb0: %s", strerror(errno));
    // The Snapmaker app may have left a flipped page on screen: draw where
    // the display currently looks.
    fb = fb_map + (size_t)var.yoffset * stride + (size_t)var.xoffset * bytes_pp;
    origin_x = (var.xres - SCALED_W) / 2;
    origin_y = (var.yres - SCALED_H) / 2;
    for (int x = 0; x < SCALED_W; x++)
        src_x[x] = x * 2 / 3;
    for (int y = 0; y < SCALED_H; y++)
        src_y[y] = y * 2 / 3;

    clear_screen();
    atexit(clear_screen);
    open_inputs();
    // Ctrl+C over SSH must still end the game; SDL would turn it into an event.
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");
    if (SDL_Init(SDL_INIT_GAMECONTROLLER) != 0)
        printf("No game controllers: %s\n", SDL_GetError());
    clock_gettime(CLOCK_MONOTONIC, &start);
}

void DG_DrawFrame(void)
{
    for (int y = 0; y < SCALED_H; y++) {
        const uint32_t *in = DG_ScreenBuffer + src_y[y] * DOOMGENERIC_RESX;
        uint8_t *out = fb + (size_t)(origin_y + y) * stride + origin_x * bytes_pp;

        for (int x = 0; x < SCALED_W; x++) {
            uint32_t p = in[src_x[x]];   // XRGB8888 from i_video.c
            uint32_t r = (p >> 16) & 0xff, g = (p >> 8) & 0xff, b = p & 0xff;
            uint32_t px = (r >> (8 - var.red.length)) << var.red.offset
                        | (g >> (8 - var.green.length)) << var.green.offset
                        | (b >> (8 - var.blue.length)) << var.blue.offset;
            if (bytes_pp == 4)
                ((uint32_t *)out)[x] = px;
            else
                ((uint16_t *)out)[x] = (uint16_t)px;
        }
    }
    read_inputs();
}

void DG_SleepMs(uint32_t ms)
{
    usleep(ms * 1000);
}

uint32_t DG_GetTicksMs(void)
{
    struct timespec now;

    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint32_t)((now.tv_sec - start.tv_sec) * 1000 + (now.tv_nsec - start.tv_nsec) / 1000000);
}

int DG_GetKey(int *pressed, unsigned char *key)
{
    read_inputs();
    if (key_read == key_write)
        return 0;
    *pressed = key_queue[key_read] >> 8;
    *key = key_queue[key_read] & 0xff;
    key_read = (key_read + 1) % KEY_QUEUE;
    return 1;
}

void DG_SetWindowTitle(const char *title)
{
    (void)title;
}

int main(int argc, char **argv)
{
    doomgeneric_Create(argc, argv);
    for (;;)
        doomgeneric_Tick();
}
