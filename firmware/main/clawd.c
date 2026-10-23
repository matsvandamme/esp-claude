#include "clawd.h"

#include <string.h>

#include "esp_heap_caps.h"
#include "esp_random.h"
#include "theme.h"

// Terminal cells are ~1:2, so the banner's quarter-block pixels are twice as tall as wide.
#define UX       6                   // px per grid unit, horizontal
#define UY       11                  // px per grid unit, vertical
#define GRID_W   18
#define HEAD_U   2                   // headroom above the body for raised arms
#define CANVAS_W (GRID_W * UX)       // 108
#define CANVAS_H ((HEAD_U + 5) * UY) // 77
#define TICK_MS  50

typedef enum { EYE_OPEN, EYE_CLOSED, EYE_HAPPY, EYE_WIDE, EYE_UP, EYE_LEFT, EYE_RIGHT, EYE_X } eye_t;
typedef enum { ARM_DOWN, ARM_UP_OUT, ARM_UP } arm_t;

typedef struct {
    eye_t eye;
    arm_t arm_l, arm_r;
    uint8_t legs;   // bit0: left pair lifted, bit1: right pair lifted
    bool squish;
} pose_t;

static lv_obj_t *s_canvas;
static lv_obj_t *s_fx;          // "!" or "z" above the head
static uint16_t *s_buf;
static clawd_mood_t s_mood = CLAWD_IDLE;
static uint32_t s_t;            // ms since mood change
static uint32_t s_next_blink;
static pose_t s_drawn;
static bool s_drawn_valid;
static int32_t s_base_y;
static uint16_t s_body565, s_bg565;

static uint16_t rgb565(uint32_t rgb)
{
    return (uint16_t)(((rgb >> 8) & 0xF800) | ((rgb >> 5) & 0x07E0) | ((rgb & 0xFF) >> 3));
}

static void fill(int x, int y, int w, int h, uint16_t c)
{
    for (int yy = y; yy < y + h; yy++) {
        if (yy < 0 || yy >= CANVAS_H) {
            continue;
        }
        uint16_t *row = s_buf + yy * CANVAS_W;
        for (int xx = x; xx < x + w; xx++) {
            if (xx >= 0 && xx < CANVAS_W) {
                row[xx] = c;
            }
        }
    }
}

// Grid-unit rect (row 0 = first body row).
static void cell(int gx, int gy, int gw, int gh, uint16_t c)
{
    fill(gx * UX, (gy + HEAD_U) * UY, gw * UX, gh * UY, c);
}

static void draw_eye(int gx, int gy, eye_t e)
{
    int x = gx * UX, y = (gy + HEAD_U) * UY;
    uint16_t bg = s_bg565, white = rgb565(C_TEXT);
    switch (e) {
    case EYE_OPEN:
        fill(x, y + 1, UX, UY - 1, bg);
        break;
    case EYE_CLOSED:
        fill(x, y + UY - 4, UX, 3, bg);
        break;
    case EYE_HAPPY:  // ^
        fill(x, y + 5, 2, 4, bg);
        fill(x + 2, y + 2, 2, 4, bg);
        fill(x + 4, y + 5, 2, 4, bg);
        break;
    case EYE_WIDE:
        fill(x, y - 2, UX, UY + 2, bg);
        fill(x + 1, y, 2, 3, white);
        break;
    case EYE_UP:
        fill(x, y - 1, UX, 6, bg);
        break;
    case EYE_LEFT:
        fill(x, y + 1, 3, UY - 1, bg);
        break;
    case EYE_RIGHT:
        fill(x + 3, y + 1, 3, UY - 1, bg);
        break;
    case EYE_X:
        fill(x, y + 1, 2, 3, bg);
        fill(x + 4, y + 1, 2, 3, bg);
        fill(x + 2, y + 4, 2, 3, bg);
        fill(x, y + 7, 2, 3, bg);
        fill(x + 4, y + 7, 2, 3, bg);
        break;
    }
}

static void draw_arm(bool left, arm_t a, int arm_row)
{
    uint16_t c = s_body565;
    int x_in = left ? 2 : 15, x_out = left ? 1 : 16;
    switch (a) {
    case ARM_DOWN:
        cell(left ? 1 : 15, arm_row, 2, 1, c);
        break;
    case ARM_UP_OUT:
        cell(x_in, arm_row - 1, 1, 1, c);
        cell(x_out, arm_row - 2, 1, 1, c);
        break;
    case ARM_UP:
        cell(x_in, arm_row - 1, 1, 1, c);
        cell(x_in, arm_row - 2, 1, 1, c);
        break;
    }
}

static void render(const pose_t *p)
{
    uint16_t c = s_body565;
    fill(0, 0, CANVAS_W, CANVAS_H, s_bg565);
    int top = p->squish ? 1 : 0;  // squished body drops its first row
    // Body: rows top..3, arms on row 2, legs on row 4.
    for (int r = top; r < 4; r++) {
        cell(3, r, 12, 1, c);
    }
    int eye_row = p->squish ? 2 : 1;
    draw_eye(5, eye_row, p->eye);
    draw_eye(12, eye_row, p->eye);
    draw_arm(true, p->arm_l, 2);
    draw_arm(false, p->arm_r, 2);
    static const int legs_x[4] = {4, 6, 11, 13};
    for (int i = 0; i < 4; i++) {
        bool lifted = (i % 2 == 0) ? (p->legs & 1) : (p->legs & 2);
        if (lifted) {
            fill(legs_x[i] * UX, (4 + HEAD_U) * UY, UX, UY / 2, c);
        } else {
            cell(legs_x[i], 4, 1, 1, c);
        }
    }
    lv_obj_invalidate(s_canvas);
}

static bool pose_eq(const pose_t *a, const pose_t *b)
{
    return a->eye == b->eye && a->arm_l == b->arm_l && a->arm_r == b->arm_r && a->legs == b->legs &&
           a->squish == b->squish;
}

static uint32_t rnd(uint32_t lo, uint32_t hi)
{
    return lo + esp_random() % (hi - lo + 1);
}

static eye_t idle_eyes(eye_t open)
{
    // Blink for 150 ms every 3-6 s.
    if (s_t >= s_next_blink) {
        if (s_t < s_next_blink + 150) {
            return EYE_CLOSED;
        }
        s_next_blink = s_t + rnd(3000, 6000);
    }
    return open;
}

static pose_t pose_for(clawd_mood_t m)
{
    pose_t p = {.eye = EYE_OPEN, .arm_l = ARM_DOWN, .arm_r = ARM_DOWN};
    uint32_t t = s_t;
    switch (m) {
    case CLAWD_IDLE: {
        uint32_t ph = t % 10000;
        eye_t look = ph > 7000 && ph < 7700 ? EYE_LEFT : (ph >= 7700 && ph < 8400 ? EYE_RIGHT : EYE_OPEN);
        p.eye = idle_eyes(look);
        break;
    }
    case CLAWD_THINK:
        p.eye = idle_eyes((t / 1400) % 3 == 1 ? EYE_LEFT : ((t / 1400) % 3 == 2 ? EYE_RIGHT : EYE_UP));
        break;
    case CLAWD_WORK:
        p.eye = idle_eyes(EYE_OPEN);
        p.legs = ((t / 150) % 2) ? 1 : 2;
        p.arm_l = ((t / 300) % 2) ? ARM_UP_OUT : ARM_DOWN;
        p.arm_r = ((t / 300) % 2) ? ARM_DOWN : ARM_UP_OUT;
        break;
    case CLAWD_ALERT:
        p.eye = EYE_WIDE;
        p.arm_r = ((t / 260) % 2) ? ARM_UP : ARM_UP_OUT;
        break;
    case CLAWD_ALERT_URGENT:
        p.eye = EYE_WIDE;
        p.arm_l = ((t / 130) % 2) ? ARM_UP_OUT : ARM_UP;
        p.arm_r = ((t / 130) % 2) ? ARM_UP : ARM_UP_OUT;
        p.legs = ((t / 130) % 2) ? 1 : 2;
        break;
    case CLAWD_HAPPY:
        p.eye = idle_eyes(EYE_HAPPY);
        if (t < 2400) {
            bool up = (t / 300) % 2;
            p.arm_l = up ? ARM_UP_OUT : ARM_UP;
            p.arm_r = up ? ARM_UP_OUT : ARM_UP;
        }
        break;
    case CLAWD_ERROR:
        p.eye = EYE_X;
        break;
    case CLAWD_SQUISH:
        p.eye = EYE_CLOSED;
        p.squish = (t / 450) % 2;
        break;
    case CLAWD_SLEEP:
        p.eye = EYE_CLOSED;
        break;
    }
    return p;
}

static void tick_cb(lv_timer_t *timer)
{
    s_t += TICK_MS;
    pose_t p = pose_for(s_mood);
    if (!s_drawn_valid || !pose_eq(&p, &s_drawn)) {
        render(&p);
        s_drawn = p;
        s_drawn_valid = true;
    }
    if (s_mood == CLAWD_ALERT_URGENT) {
        bool on = (s_t / 250) % 2;
        lv_obj_set_style_opa(s_fx, on ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
    }
}

static void bob_cb(void *obj, int32_t v)
{
    lv_obj_set_y((lv_obj_t *)obj, s_base_y + v);
}

static void fx_float_cb(void *obj, int32_t v)
{
    // v: 0..1000 over one "z" lifetime.
    lv_obj_set_y((lv_obj_t *)obj, s_base_y + 4 - v * 18 / 1000);
    lv_obj_set_style_opa((lv_obj_t *)obj, (lv_opa_t)(v < 200 ? v * 255 / 200 : (1000 - v) * 255 / 800), 0);
}

static void start_bob(int32_t amp, uint32_t period_ms)
{
    lv_anim_delete(s_canvas, bob_cb);
    lv_obj_set_y(s_canvas, s_base_y);
    if (amp == 0) {
        return;
    }
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_canvas);
    lv_anim_set_exec_cb(&a, bob_cb);
    lv_anim_set_values(&a, 0, -amp);
    lv_anim_set_duration(&a, period_ms / 2);
    lv_anim_set_playback_duration(&a, period_ms / 2);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_start(&a);
}

static void set_fx(const char *text, uint32_t color, bool floating)
{
    lv_anim_delete(s_fx, fx_float_cb);
    if (!text) {
        lv_obj_add_flag(s_fx, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    lv_label_set_text(s_fx, text);
    lv_obj_set_style_text_color(s_fx, lv_color_hex(color), 0);
    lv_obj_set_style_opa(s_fx, LV_OPA_COVER, 0);
    lv_obj_remove_flag(s_fx, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_y(s_fx, s_base_y - 2);
    if (floating) {
        lv_anim_t a;
        lv_anim_init(&a);
        lv_anim_set_var(&a, s_fx);
        lv_anim_set_exec_cb(&a, fx_float_cb);
        lv_anim_set_values(&a, 0, 1000);
        lv_anim_set_duration(&a, 2200);
        lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
        lv_anim_start(&a);
    }
}

void clawd_set_mood(clawd_mood_t mood)
{
    if (mood == s_mood && s_drawn_valid) {
        return;
    }
    s_mood = mood;
    s_t = 0;
    s_next_blink = rnd(1500, 3000);
    switch (mood) {
    case CLAWD_IDLE:
        start_bob(2, 2600);
        set_fx(NULL, 0, false);
        break;
    case CLAWD_THINK:
        start_bob(3, 1200);
        set_fx(NULL, 0, false);
        break;
    case CLAWD_WORK:
        start_bob(2, 420);
        set_fx(NULL, 0, false);
        break;
    case CLAWD_ALERT:
        start_bob(4, 600);
        set_fx("!", C_WARNING, false);
        break;
    case CLAWD_ALERT_URGENT:
        start_bob(6, 320);
        set_fx("!", C_ERROR, false);
        break;
    case CLAWD_HAPPY:
        start_bob(3, 1800);
        set_fx(NULL, 0, false);
        break;
    case CLAWD_ERROR:
        start_bob(0, 0);
        set_fx(NULL, 0, false);
        break;
    case CLAWD_SQUISH:
        start_bob(1, 900);
        set_fx(NULL, 0, false);
        break;
    case CLAWD_SLEEP:
        start_bob(1, 3200);
        set_fx("z", C_INACTIVE, true);
        break;
    }
    tick_cb(NULL);
}

clawd_mood_t clawd_get_mood(void)
{
    return s_mood;
}

void clawd_create(lv_obj_t *parent, int32_t x, int32_t y)
{
    s_body565 = rgb565(C_CLAUDE);
    s_bg565 = rgb565(C_BG);
    s_buf = heap_caps_malloc(CANVAS_W * CANVAS_H * 2, MALLOC_CAP_8BIT);
    assert(s_buf);
    s_base_y = y;
    s_canvas = lv_canvas_create(parent);
    lv_canvas_set_buffer(s_canvas, s_buf, CANVAS_W, CANVAS_H, LV_COLOR_FORMAT_RGB565);
    lv_obj_set_pos(s_canvas, x, y);

    s_fx = lv_label_create(parent);
    lv_obj_set_style_text_font(s_fx, &font_mono_20b, 0);
    lv_obj_set_pos(s_fx, x + 15 * UX, y - 2);
    lv_obj_add_flag(s_fx, LV_OBJ_FLAG_HIDDEN);

    lv_timer_create(tick_cb, TICK_MS, NULL);
    s_drawn_valid = false;
    clawd_set_mood(CLAWD_IDLE);
}
