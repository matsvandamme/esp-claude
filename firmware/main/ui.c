#include "ui.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

#include "board.h"
#include "clawd.h"
#include "display.h"
#include "esp_timer.h"
#include "led.h"
#include "nvs.h"
#include "ota.h"
#include "snap.h"
#include "theme.h"

// Layout (320x172, "gauge stack", D38):
//   header   project name                         ⚠ other session badge | ✗wifi
//   strip    3 px in the focused session's state colour
//   body     Clawd   ✻ Verb…            (status)
//                    ● Tool · target    (current tool)
//                    0:42 · 2 agents    (turn timer)
//            5h   ████████░░░  77% left   2h14m
//            week █████░░░░░░  59% left   3d 4h
//            ctx  ██████░░░░░  52% used
//   The "needs you" / error / limit banner covers the gauge stack.

#define X_RIGHT        124
#define W_RIGHT        192
#define Y_BODY         24   // the body container slides in when the project changes
#define Y_GAUGE        83   // body-relative top of the first gauge row
#define GAUGE_PITCH    21
#define Y_BANNER       105  // screen y where the banner starts (covers the gauges)
#define SLIDE_PX       40
#define SLIDE_MS       220
#define OFFLINE_US     (5LL * 1000000)
#define BL_HIGH        100  // backlight %, fixed: only a long BOOT press changes it (D45)
#define BL_LOW         60
#define ESCALATE_S     60
#define IDLE_VIEW_S    120  // nothing working this long: big clock + today (D46)
#define LONG_TOOL_S    180  // a tool running this long gets an amber "still running" (D50)
#define LOW_LEFT_PCT   15   // plan window dropping below this much left: one LED pulse (D47)
#define OVERVIEW_US    (3LL * 1000000)  // session list after a BOOT press (D51)
#define ROW_H          18

static const char *SPIN[] = {G_DOT, "\xE2\x9C\xA2", "\xE2\x9C\xB3", "\xE2\x9C\xB6", "\xE2\x9C\xBB", "\xE2\x9C\xBD"};
#define SPIN_N 6

typedef struct {
    lv_obj_t *name, *bar, *val, *unit, *reset;
} gauge_t;

enum { G_5H = 0, G_WEEK, G_CTX, G_N };

static lv_obj_t *lbl_title, *lbl_right, *strip, *body;
static lv_obj_t *lbl_spin, *lbl_verb, *lbl_tb, *lbl_tt, *lbl_meta;
static gauge_t gauges[G_N];
static lv_obj_t *banner, *lbl_b1, *lbl_b2;
static lv_obj_t *lbl_clock, *lbl_big, *lbl_date, *lbl_today;
static lv_obj_t *ov, *ov_title;
static struct {
    lv_obj_t *bg, *dot, *name, *state, *age;
} ov_rows[SNAP_MAX_SESS];

static snap_t s_snap;
static bool s_have_snap;
static int64_t s_snap_rx_us;    // device time the current snapshot arrived
static int64_t s_state_since_us;
static bool s_offline = true;
static int s_spin_i, s_spin_dir = 1;
static bool s_shimmer_on;
static bool s_bl_low;  // kept in NVS so it survives a reboot
static bool s_time_known;
static int64_t s_epoch_off_s;  // UTC epoch minus uptime, from the host (D46)
static int32_t s_tz_s;         // local offset from UTC
static bool s_idle;            // the idle view is on screen
static int64_t s_overview_until_us;

// ---------- helpers ----------

static void set_text(lv_obj_t *l, const char *t)
{
    if (strcmp(lv_label_get_text(l), t) != 0) {
        lv_label_set_text(l, t);
    }
}

static void set_color(lv_obj_t *o, uint32_t rgb)
{
    lv_color_t c = lv_color_hex(rgb);
    if (!lv_color_eq(lv_obj_get_style_text_color(o, 0), c)) {
        lv_obj_set_style_text_color(o, c, 0);
    }
}

static void show(lv_obj_t *o, bool on)
{
    if (on == lv_obj_has_flag(o, LV_OBJ_FLAG_HIDDEN)) {
        if (on) {
            lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
        }
    }
}

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font, uint32_t color, int x, int y, int w)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
    lv_obj_set_pos(l, x, y);
    if (w > 0) {
        // Fixed single-line box: LONG_DOT only truncates when the height is fixed too.
        lv_obj_set_size(l, w, lv_font_get_line_height(font));
        lv_label_set_long_mode(l, LV_LABEL_LONG_DOT);
    }
    lv_label_set_text(l, "");
    return l;
}

static lv_obj_t *rect(lv_obj_t *parent, int x, int y, int w, int h, uint32_t color)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    lv_obj_set_style_bg_color(o, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    return o;
}

static lv_obj_t *bar(lv_obj_t *parent, int x, int y, int w, int h)
{
    lv_obj_t *b = lv_bar_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_set_pos(b, x, y);
    lv_obj_set_size(b, w, h);
    lv_obj_set_style_bg_color(b, lv_color_hex(C_RL_EMPTY), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(b, 2, LV_PART_MAIN);
    lv_obj_set_style_bg_color(b, lv_color_hex(C_RL_FILL), LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_radius(b, 2, LV_PART_INDICATOR);
    lv_bar_set_range(b, 0, 100);
    return b;
}

static void fmt_countdown(char *buf, size_t n, int32_t s)
{
    if (s < 0) {
        buf[0] = 0;
    } else if (s < 3600) {
        snprintf(buf, n, "%ldm", (long)((s + 59) / 60));
    } else if (s < 86400) {
        snprintf(buf, n, "%ldh%02ldm", (long)(s / 3600), (long)((s / 60) % 60));
    } else {
        snprintf(buf, n, "%ldd %ldh", (long)(s / 86400), (long)((s / 3600) % 24));
    }
}

static int32_t since_snap_s(void)
{
    return (int32_t)((esp_timer_get_time() - s_snap_rx_us) / 1000000);
}

static int32_t live_up(int32_t base_s)
{
    return base_s + since_snap_s();
}

static int32_t live_down(int32_t base_s)
{
    if (base_s < 0) {
        return -1;
    }
    int32_t v = base_s - since_snap_s();
    return v < 0 ? 0 : v;
}

static bool local_time(struct tm *out)
{
    if (!s_time_known) {
        return false;
    }
    time_t t = (time_t)(esp_timer_get_time() / 1000000 + s_epoch_off_s + s_tz_s);
    gmtime_r(&t, out);
    return true;
}

static void fmt_age(char *buf, size_t n, int32_t s)
{
    if (s < 60) {
        snprintf(buf, n, "%lds", (long)s);
    } else if (s < 3600) {
        snprintf(buf, n, "%ldm", (long)(s / 60));
    } else if (s < 86400) {
        snprintf(buf, n, "%ldh%02ldm", (long)(s / 3600), (long)((s / 60) % 60));
    } else {
        snprintf(buf, n, "%ldd", (long)(s / 86400));
    }
}

static void fmt_tokens(char *buf, size_t n, int32_t k)
{
    if (k < 1000) {
        snprintf(buf, n, "%ld", (long)k);
    } else if (k < 100000) {
        snprintf(buf, n, "%ld.%ldk", (long)(k / 1000), (long)((k % 1000) / 100));
    } else if (k < 1000000) {
        snprintf(buf, n, "%ldk", (long)(k / 1000));
    } else {
        snprintf(buf, n, "%ld.%ldM", (long)(k / 1000000), (long)((k % 1000000) / 100000));
    }
}

static bool long_tool(void)
{
    return !s_offline && s_snap.has_focus && s_snap.st == ST_TOOL && s_snap.tool_age_s >= 0 &&
           live_up(s_snap.tool_age_s) >= LONG_TOOL_S;
}

static bool done_away(void)
{
    return s_snap.has_focus && s_snap.done_away_s >= 0;
}

// D46: nothing is working (no session, or the one on screen idle/done for 2 min) and no
// banner needs the space: show the time and today's numbers instead of an empty status.
static bool idle_view(void)
{
    if (s_offline || !s_have_snap || !s_time_known || ota_progress() >= 0) {
        return false;
    }
    if (!s_snap.has_focus) {
        return true;
    }
    if (s_snap.st != ST_IDLE && s_snap.st != ST_DONE) {
        return false;
    }
    int32_t age = (int32_t)((esp_timer_get_time() - s_state_since_us) / 1000000);
    if (s_snap.focus_idx >= 0 && s_snap.focus_idx < s_snap.nrows) {
        age = live_up(s_snap.rows[s_snap.focus_idx].age_s);  // survives a display reboot
    }
    return age >= IDLE_VIEW_S;
}

static bool escalated(void)
{
    return s_have_snap && s_snap.has_focus && snap_state_needs_user(s_snap.st) &&
           live_up(s_snap.wait_s) >= ESCALATE_S;
}

// ---------- animations ----------

static void shimmer_cb(void *obj, int32_t v)
{
    lv_obj_set_style_text_color(obj, lv_color_mix(lv_color_hex(C_TEXT), lv_color_hex(C_CLAUDE), (uint8_t)v), 0);
}

static void set_shimmer(bool on)
{
    if (on == s_shimmer_on) {
        return;
    }
    s_shimmer_on = on;
    lv_anim_delete(lbl_verb, shimmer_cb);
    if (!on) {
        return;
    }
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, lbl_verb);
    lv_anim_set_exec_cb(&a, shimmer_cb);
    lv_anim_set_values(&a, 40, 200);
    lv_anim_set_duration(&a, 900);
    lv_anim_set_playback_duration(&a, 900);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_start(&a);
}

static void bar_anim_cb(void *b, int32_t v)
{
    lv_bar_set_value(b, v, LV_ANIM_OFF);
}

static void set_bar(lv_obj_t *b, int32_t pct, uint32_t color)
{
    if (pct < 0) {
        pct = 0;
    } else if (pct > 100) {
        pct = 100;
    }
    lv_obj_set_style_bg_color(b, lv_color_hex(color), LV_PART_INDICATOR);
    int32_t cur = lv_bar_get_value(b);
    if (cur == pct) {
        return;
    }
    lv_anim_delete(b, bar_anim_cb);
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, b);
    lv_anim_set_exec_cb(&a, bar_anim_cb);
    lv_anim_set_values(&a, cur, pct);
    lv_anim_set_duration(&a, 600);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_start(&a);
}

// ---------- rendering ----------

static void render_power(void)
{
    uint8_t want = s_bl_low ? BL_LOW : BL_HIGH;
    if (display_get_backlight() != want) {
        display_set_backlight(want);
    }
}

static void load_brightness(void)
{
    nvs_handle_t h;
    uint8_t v = 0;
    if (nvs_open("ui", NVS_READONLY, &h) == ESP_OK) {
        nvs_get_u8(h, "bl_low", &v);
        nvs_close(h);
    }
    s_bl_low = v != 0;
}

static void save_brightness(void)
{
    nvs_handle_t h;
    if (nvs_open("ui", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_u8(h, "bl_low", s_bl_low);
        nvs_commit(h);
        nvs_close(h);
    }
}

static void render_led(void)
{
    if (s_offline || !s_have_snap) {
        led_set_mode(LED_OFF);
    } else if (s_snap.has_focus && snap_state_needs_user(s_snap.st)) {
        led_set_mode(escalated() ? LED_ALERT_URGENT : LED_ALERT);
    } else if (s_snap.has_other) {
        led_set_mode(LED_ALERT);
    } else if (s_snap.has_focus && s_snap.st == ST_ERROR) {
        led_set_mode(LED_ERROR);
    } else if (s_snap.has_focus && s_snap.st == ST_LIMIT) {
        led_set_mode(LED_LIMIT);
    } else if (s_snap.has_focus && s_snap.st == ST_DONE && esp_timer_get_time() - s_state_since_us < 4000000) {
        led_set_mode(LED_DONE);
    } else if (done_away() || s_snap.has_done_other) {
        led_set_mode(LED_DONE_AWAY);
    } else {
        led_set_mode(LED_OFF);
    }
}

static void fmt_elapsed(char *buf, size_t n, int32_t s)
{
    if (s < 3600) {
        snprintf(buf, n, "%ld:%02ld", (long)(s / 60), (long)(s % 60));
    } else {
        snprintf(buf, n, "%ldh%02ldm", (long)(s / 3600), (long)((s / 60) % 60));
    }
}

static void gauge_show(gauge_t *g, bool on)
{
    show(g->name, on);
    show(g->bar, on);
    show(g->val, on);
    show(g->unit, on);
    show(g->reset, on);
}

// The gauge stack: what is left of the 5-hour session and the week (with reset countdowns),
// and how full the focused session's context is.
static void render_gauges(void)
{
    const int16_t used[2] = {s_snap.h5, s_snap.d7};
    const int32_t reset[2] = {s_snap.h5_reset_s, s_snap.d7_reset_s};
    char buf[24];
    for (int i = 0; i < 2; i++) {
        gauge_t *g = &gauges[i];
        bool on = !s_offline && s_have_snap && used[i] >= 0;
        gauge_show(g, on);
        if (!on) {
            continue;
        }
        int left = 100 - used[i];
        if (left < 0) {
            left = 0;
        }
        uint32_t c = left > 30 ? C_RL_FILL : (left > 10 ? C_WARNING : C_ERROR);
        set_bar(g->bar, left, c);
        snprintf(buf, sizeof(buf), "%d%%", left);
        set_text(g->val, buf);
        set_color(g->val, left > 30 ? C_TEXT : c);
        fmt_countdown(buf, sizeof(buf), live_down(reset[i]));
        set_text(g->reset, buf);
    }
    gauge_t *g = &gauges[G_CTX];
    bool ctx_on = !s_offline && s_have_snap && s_snap.has_focus && s_snap.ctx_pct >= 0 && !s_idle;
    gauge_show(g, ctx_on);
    if (ctx_on) {
        // D48: from 80 % the context needs a /compact soon; at 95 % auto-compact is close.
        int pct = s_snap.ctx_pct > 100 ? 100 : s_snap.ctx_pct;
        uint32_t c = pct < 80 ? C_RL_FILL : (pct < 95 ? C_WARNING : C_ERROR);
        set_bar(g->bar, pct, c);
        snprintf(buf, sizeof(buf), "%d%%", s_snap.ctx_pct);
        set_text(g->val, buf);
        set_color(g->val, pct < 80 ? C_TEXT : c);
        show(g->unit, pct < 80);
        set_text(g->reset, pct < 80 ? "" : "\xE2\x86\x92 compact");  // →
        set_color(g->reset, c);
    }
    show(lbl_today, s_idle && s_snap.has_today);
    if (s_idle && s_snap.has_today) {
        char act[16], tok[16];
        int32_t a = s_snap.today_active_s;
        if (a < 3600) {
            snprintf(act, sizeof(act), "%ldm", (long)(a / 60));
        } else {
            snprintf(act, sizeof(act), "%ldh%02ldm", (long)(a / 3600), (long)((a / 60) % 60));
        }
        fmt_tokens(tok, sizeof(tok), s_snap.today_tokens);
        snprintf(buf, sizeof(buf), "%ld turn%s", (long)s_snap.today_turns, s_snap.today_turns == 1 ? "" : "s");
        char line[64];
        snprintf(line, sizeof(line), "today  %s " G_DOT " %s " G_DOT " %s tok", buf, act, tok);
        set_text(lbl_today, line);
    }
}

static void render_header_right(void)
{
    char buf[48];
    if (!s_offline && s_snap.has_other) {
        if (s_snap.other_count > 1) {
            snprintf(buf, sizeof(buf), G_WARN " %d need you", s_snap.other_count);
        } else {
            snprintf(buf, sizeof(buf), G_WARN " %s", s_snap.other_project);
        }
        set_text(lbl_right, buf);
        set_color(lbl_right, C_WARNING);
    } else if (!s_offline && s_snap.has_done_other) {
        if (s_snap.done_other_count > 1) {
            snprintf(buf, sizeof(buf), G_CHECK " %d done", s_snap.done_other_count);
        } else {
            snprintf(buf, sizeof(buf), G_CHECK " %s", s_snap.done_other_project);
        }
        set_text(lbl_right, buf);
        set_color(lbl_right, C_SUCCESS);
    } else if (!s_offline && s_snap.wifi_blocked) {
        set_text(lbl_right, G_CROSS "wifi");
        set_color(lbl_right, C_WARNING);
    } else {
        set_text(lbl_right, "");
    }
}

static uint32_t state_color(sess_state_t st)
{
    switch (st) {
    case ST_START:
    case ST_THINK:
    case ST_TOOL:
        return C_CLAUDE;
    case ST_COMPACT:
        return C_PLAN;
    case ST_PERM:
    case ST_ASK:
        return C_PERMISSION;
    case ST_DONE:
        return C_SUCCESS;
    case ST_ERROR:
        return C_ERROR;
    case ST_LIMIT:
        return C_RL_FILL;
    default:
        return C_SUBTLE;
    }
}

static uint32_t strip_color(void)
{
    if (s_offline || !s_snap.has_focus) {
        return C_SUBTLE;
    }
    if (s_snap.color >= 0) {
        return (uint32_t)s_snap.color;  // D41: the session's /color, like Claude Code's prompt bar
    }
    return state_color(s_snap.st);
}

static void render_clock(void)
{
    struct tm tm;
    char buf[24];
    if (!local_time(&tm)) {
        set_text(lbl_clock, "");
        return;
    }
    snprintf(buf, sizeof(buf), "%02d:%02d", tm.tm_hour, tm.tm_min);
    set_text(lbl_clock, buf);
    if (s_idle) {
        static const char *DAYS[] = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
        static const char *MONTHS[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
        set_text(lbl_big, buf);
        snprintf(buf, sizeof(buf), "%s %d %s", DAYS[tm.tm_wday % 7], tm.tm_mday, MONTHS[tm.tm_mon % 12]);
        set_text(lbl_date, buf);
    }
}

static void set_bg(lv_obj_t *o, uint32_t rgb)
{
    lv_color_t c = lv_color_hex(rgb);
    if (!lv_color_eq(lv_obj_get_style_bg_color(o, 0), c)) {
        lv_obj_set_style_bg_color(o, c, 0);
    }
}

// Everything that moves with time between snapshots: countdowns, the turn timer and the
// "needs you" escalation.
static void render_timers(void)
{
    char buf[48];
    render_gauges();
    int ota = ota_progress();
    if (ota >= 0) {
        show(banner, true);
        set_bg(banner, C_PLAN);
        snprintf(buf, sizeof(buf), "Updating" G_ELLIPSIS " %d%%", ota);
        set_text(lbl_b1, buf);
        set_text(lbl_b2, "new firmware over WiFi " G_DOT " don't unplug");
        return;
    }
    if (s_offline || !s_snap.has_focus) {
        return;
    }
    const snap_t *s = &s_snap;
    if (long_tool()) {
        char el[16];
        fmt_elapsed(el, sizeof(el), live_up(s->tool_age_s));
        snprintf(buf, sizeof(buf), G_WARN " still running %s", el);
        set_text(lbl_meta, buf);
        set_color(lbl_meta, C_WARNING);
        set_color(lbl_tb, C_WARNING);
        lv_obj_set_style_opa(lbl_tb, LV_OPA_COVER, 0);
    } else if (snap_state_working(s->st)) {
        fmt_elapsed(buf, sizeof(buf), live_up(s->elapsed_s));
        if (s->nsub > 0) {
            size_t n = strlen(buf);
            snprintf(buf + n, sizeof(buf) - n, " " G_DOT " %d agent%s", s->nsub, s->nsub > 1 ? "s" : "");
        }
        set_text(lbl_meta, buf);
        set_color(lbl_meta, C_INACTIVE);
    } else if (s->st == ST_LIMIT) {
        int32_t r = live_down(s->limit_reset_s);
        if (r >= 0) {
            char cd[16];
            fmt_countdown(cd, sizeof(cd), r);
            snprintf(buf, sizeof(buf), "Back in %s", cd);
        } else {
            snprintf(buf, sizeof(buf), "Usage limit reached");
        }
        set_text(lbl_b1, buf);
    } else if (done_away() && !snap_state_needs_user(s->st) && s->st != ST_ERROR) {
        char ago[16];
        int32_t a = live_up(s->done_away_s);
        if (a < 60) {
            snprintf(ago, sizeof(ago), "just now");
        } else {
            fmt_age(ago, sizeof(ago), a);
            strlcat(ago, " ago", sizeof(ago));
        }
        if (s->st == ST_DONE && s->elapsed_s > 0) {
            char el[16];
            fmt_elapsed(el, sizeof(el), s->elapsed_s);
            snprintf(buf, sizeof(buf), "finished %s " G_DOT " took %s", ago, el);
        } else {
            snprintf(buf, sizeof(buf), "finished %s", ago);
        }
        set_text(lbl_b2, buf);
    }
    if (!snap_state_needs_user(s->st)) {
        set_bg(strip, strip_color());
        return;
    }
    int32_t w = live_up(s->wait_s);
    bool esc = w >= ESCALATE_S;
    clawd_set_mood(esc ? CLAWD_ALERT_URGENT : CLAWD_ALERT);
    snprintf(buf, sizeof(buf), G_WARN " Needs you " G_DOT " %ld:%02ld", (long)(w / 60), (long)(w % 60));
    set_text(lbl_b1, buf);
    uint32_t c = C_CLAUDE;
    if (esc) {
        c = (esp_timer_get_time() / 400000) % 2 ? C_WARNING : C_CLAUDE;
    }
    set_bg(banner, c);
    set_bg(strip, esc ? c : C_PERMISSION);
}

static void show_tool(bool on)
{
    show(lbl_tb, on);
    show(lbl_tt, on);
}

static void hint(const char *text)
{
    show_tool(true);
    lv_obj_set_style_opa(lbl_tb, LV_OPA_TRANSP, 0);
    set_text(lbl_tt, text);
    set_color(lbl_tt, C_INACTIVE);
}

// The idle view swaps the status lines for the big clock; everything else stays.
static void apply_idle(void)
{
    show(lbl_big, s_idle);
    show(lbl_date, s_idle);
    show(lbl_spin, !s_idle);
    show(lbl_verb, !s_idle);
    show(lbl_meta, !s_idle);
    if (s_idle) {
        show_tool(false);
        set_shimmer(false);
        clawd_set_mood(CLAWD_SLEEP);
    }
    render_clock();
}

static void render(void)
{
    char buf[96];
    s_idle = idle_view();
    render_header_right();

    if (s_offline || !s_snap.has_focus) {
        set_text(lbl_title, "claude-display");
        set_color(lbl_title, C_INACTIVE);
        set_text(lbl_spin, "");
        set_shimmer(false);
        set_text(lbl_verb, s_offline ? "Bridge offline" : "No sessions");
        set_color(lbl_verb, C_INACTIVE);
        hint(s_offline ? "waiting for the Mac" G_ELLIPSIS : "start claude in Ghostty");
        set_text(lbl_meta, "");
        show(banner, false);
        clawd_set_mood(CLAWD_SLEEP);
        apply_idle();
        render_timers();
        set_bg(strip, strip_color());
        render_power();
        render_led();
        return;
    }
    lv_obj_set_style_opa(lbl_tb, LV_OPA_COVER, 0);

    const snap_t *s = &s_snap;
    set_text(lbl_title, s->project);
    set_color(lbl_title, s->color >= 0 ? (uint32_t)s->color : C_INACTIVE);

    // Status line: glyph + verb.
    set_shimmer(snap_state_working(s->st));
    switch (s->st) {
    case ST_START:
    case ST_THINK:
    case ST_TOOL:
        snprintf(buf, sizeof(buf), "%s" G_ELLIPSIS, s->verb[0] ? s->verb : "Thinking");
        set_text(lbl_verb, buf);
        set_color(lbl_spin, C_CLAUDE);
        break;
    case ST_COMPACT:
        set_text(lbl_verb, "Compacting" G_ELLIPSIS);
        set_color(lbl_spin, C_PLAN);
        break;
    case ST_PERM:
    case ST_ASK:
        set_text(lbl_spin, G_WARN);
        set_color(lbl_spin, C_WARNING);
        set_text(lbl_verb, s->st == ST_PERM ? "Needs permission" : "Has a question");
        set_color(lbl_verb, C_TEXT);
        break;
    case ST_DONE:
        set_text(lbl_spin, G_CHECK);
        set_color(lbl_spin, C_SUCCESS);
        if (s->elapsed_s > 0) {
            char el[16];
            fmt_elapsed(el, sizeof(el), s->elapsed_s);
            snprintf(buf, sizeof(buf), "Done in %s", el);
            set_text(lbl_verb, buf);
        } else {
            set_text(lbl_verb, "Done");
        }
        set_color(lbl_verb, C_SUCCESS);
        break;
    case ST_ERROR:
        set_text(lbl_spin, G_CROSS);
        set_color(lbl_spin, C_ERROR);
        set_text(lbl_verb, "API error");
        set_color(lbl_verb, C_ERROR);
        break;
    case ST_LIMIT:
        set_text(lbl_spin, G_BULLET);
        set_color(lbl_spin, C_RL_FILL);
        set_text(lbl_verb, "Limit reached");
        set_color(lbl_verb, C_RL_FILL);
        break;
    default:
        set_text(lbl_spin, G_BULLET);
        set_color(lbl_spin, C_INACTIVE);
        set_text(lbl_verb, "Ready");
        set_color(lbl_verb, C_TEXT);
        break;
    }

    // One line under the status: the current tool, or a hint.
    bool needs = snap_state_needs_user(s->st);
    if (needs) {
        show_tool(false);  // the banner carries the details
    } else if (s->st == ST_LIMIT) {
        hint("Claude is resting");
    } else if (s->ntl > 0 && s->st != ST_IDLE) {
        const snap_tool_t *t = &s->tools[0];
        if (t->target[0]) {
            snprintf(buf, sizeof(buf), "%s " G_DOT " %s", t->name, t->target);
        } else {
            snprintf(buf, sizeof(buf), "%s", t->name);
        }
        show_tool(true);
        set_text(lbl_tt, buf);
        set_color(lbl_tt, C_TEXT);
        set_color(lbl_tb, t->status == 1 ? C_TEXT : (t->status == 2 ? C_ERROR : C_SUCCESS));
        if (t->status != 1) {
            lv_obj_set_style_opa(lbl_tb, LV_OPA_COVER, 0);
        }
    } else if (s->st == ST_ERROR) {
        hint(s->reason[0] ? s->reason : "turn failed");
    } else {
        hint(s->st == ST_IDLE ? "waiting for your prompt" : "");
    }
    if (!snap_state_working(s->st)) {
        set_text(lbl_meta, "");
    }

    // Banner over the gauge stack.
    bool err = s->st == ST_ERROR;
    bool lim = s->st == ST_LIMIT;
    bool away = done_away() && !needs && !err && !lim;
    show(banner, needs || err || lim || away);
    if (away) {
        set_bg(banner, C_SUCCESS);  // D49: it finished while you looked elsewhere
        set_text(lbl_b1, G_CHECK " Done while away");
    } else if (err) {
        set_bg(banner, C_ERROR);
        set_text(lbl_b1, G_CROSS " API error");
        set_text(lbl_b2, s->reason[0] ? s->reason : "the turn ended with an error");
    } else if (lim) {
        set_bg(banner, C_RL_FILL);
        set_text(lbl_b2, s->d7 >= 99   ? "weekly limit " G_DOT " resumes on its own"
                         : s->h5 >= 99 ? "5-hour limit " G_DOT " resumes on its own"
                                       : "plan usage limit " G_DOT " resumes on its own");
    } else if (needs) {
        set_text(lbl_b2, s->reason[0] ? s->reason : "Claude is waiting for you");
    }

    // Mascot.
    switch (s->st) {
    case ST_START:
    case ST_THINK:
        clawd_set_mood(CLAWD_THINK);
        break;
    case ST_TOOL:
        clawd_set_mood(CLAWD_WORK);
        break;
    case ST_COMPACT:
        clawd_set_mood(CLAWD_SQUISH);
        break;
    case ST_PERM:
    case ST_ASK:
        break;  // render_timers picks alert vs urgent
    case ST_DONE:
        clawd_set_mood(CLAWD_HAPPY);
        break;
    case ST_ERROR:
        clawd_set_mood(CLAWD_ERROR);
        break;
    case ST_LIMIT:
        clawd_set_mood(CLAWD_SLEEP);
        break;
    default:
        clawd_set_mood(CLAWD_IDLE);
        break;
    }
    apply_idle();
    render_timers();
    render_power();
    render_led();
}

// A different project came into focus: slide the body in so the switch is noticed.
static void slide_cb(void *o, int32_t v)
{
    lv_obj_set_style_translate_x(o, v, 0);
    lv_obj_set_style_opa(o, (lv_opa_t)(255 - v * 255 / SLIDE_PX), 0);
}

static void slide_in(void)
{
    lv_anim_delete(body, slide_cb);
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, body);
    lv_anim_set_exec_cb(&a, slide_cb);
    lv_anim_set_values(&a, SLIDE_PX, 0);
    lv_anim_set_duration(&a, SLIDE_MS);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_start(&a);
}

// ---------- session overview (D51) ----------

static const char *state_word(sess_state_t st)
{
    switch (st) {
    case ST_START:
    case ST_THINK:
    case ST_TOOL:
        return "working";
    case ST_COMPACT:
        return "compacting";
    case ST_PERM:
    case ST_ASK:
        return "needs you";
    case ST_DONE:
        return "done";
    case ST_ERROR:
        return "error";
    case ST_LIMIT:
        return "limit";
    default:
        return "idle";
    }
}

static void render_overview(void)
{
    char buf[32];
    snprintf(buf, sizeof(buf), "Sessions (%d)", s_snap.nrows);
    set_text(ov_title, s_snap.nrows ? buf : "No sessions");
    for (int i = 0; i < SNAP_MAX_SESS; i++) {
        bool on = i < s_snap.nrows;
        show(ov_rows[i].bg, on);
        if (!on) {
            continue;
        }
        const snap_sess_t *r = &s_snap.rows[i];
        bool focused = i == s_snap.focus_idx;
        lv_obj_set_style_bg_opa(ov_rows[i].bg, focused ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
        set_color(ov_rows[i].dot, state_color(r->st));
        set_text(ov_rows[i].name, r->label);
        set_color(ov_rows[i].name, r->color >= 0 ? (uint32_t)r->color : (focused ? C_TEXT : C_INACTIVE));
        if (r->done_away) {
            snprintf(buf, sizeof(buf), "%s " G_CHECK, state_word(r->st));
        } else {
            snprintf(buf, sizeof(buf), "%s", state_word(r->st));
        }
        set_text(ov_rows[i].state, buf);
        set_color(ov_rows[i].state, r->done_away ? C_SUCCESS : (snap_state_needs_user(r->st) ? C_WARNING : C_INACTIVE));
        fmt_age(buf, sizeof(buf), live_up(r->age_s));
        set_text(ov_rows[i].age, buf);
    }
}

static void overview_create(void)
{
    ov = rect(lv_layer_top(), 0, 0, LCD_H_RES, LCD_V_RES, C_BG);
    ov_title = label(ov, &font_mono_13, C_INACTIVE, 8, 3, 300);
    rect(ov, 0, 21, LCD_H_RES, 1, C_SUBTLE);
    for (int i = 0; i < SNAP_MAX_SESS; i++) {
        int y = 24 + i * ROW_H;
        lv_obj_t *bg = rect(ov, 0, y, LCD_H_RES, ROW_H, C_PANEL);
        ov_rows[i].bg = bg;
        ov_rows[i].dot = label(bg, &font_mono_13, C_SUBTLE, 8, 1, 0);
        lv_label_set_text_static(ov_rows[i].dot, G_BULLET);
        ov_rows[i].name = label(bg, &font_mono_13, C_TEXT, 24, 1, 150);
        ov_rows[i].state = label(bg, &font_mono_13, C_INACTIVE, 178, 1, 90);
        ov_rows[i].age = label(bg, &font_mono_13, C_INACTIVE, 270, 1, 44);
        lv_obj_set_style_text_align(ov_rows[i].age, LV_TEXT_ALIGN_RIGHT, 0);
    }
    show(ov, false);
}

static bool overview_open(void)
{
    return esp_timer_get_time() < s_overview_until_us;
}

// ---------- timers ----------

static void spin_cb(lv_timer_t *t)
{
    if (s_offline || !s_snap.has_focus || !snap_state_working(s_snap.st)) {
        return;
    }
    s_spin_i += s_spin_dir;
    if (s_spin_i >= SPIN_N - 1 || s_spin_i <= 0) {
        s_spin_dir = -s_spin_dir;
    }
    lv_label_set_text_static(lbl_spin, SPIN[s_spin_i]);
}

static void slow_cb(lv_timer_t *t)
{
    bool offline = proto_last_rx_us() == 0 || esp_timer_get_time() - proto_last_rx_us() > OFFLINE_US;
    if (offline != s_offline) {
        s_offline = offline;
        render();
    }
    if (idle_view() != s_idle) {
        render();
    }
    render_timers();
    render_clock();
    render_power();
    render_led();
    if (overview_open()) {
        render_overview();
    } else {
        show(ov, false);
    }
    // Blink the bullet of a running tool.
    if (!s_offline && s_snap.has_focus && s_snap.ntl > 0 && s_snap.tools[0].status == 1 &&
        !snap_state_needs_user(s_snap.st) && !long_tool()) {
        bool phase = (esp_timer_get_time() / 500000) % 2;
        lv_obj_set_style_opa(lbl_tb, phase ? LV_OPA_COVER : LV_OPA_30, 0);
    }
}

// ---------- public ----------

void ui_init(void)
{
    lv_obj_t *scr = lv_screen_active();
    lv_obj_clean(scr);
    lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(scr, lv_color_hex(C_BG), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);

    lbl_title = label(scr, &font_mono_13, C_INACTIVE, 8, 2, 150);
    lbl_right = label(scr, &font_mono_11, C_INACTIVE, 160, 4, 116);
    lv_obj_set_style_text_align(lbl_right, LV_TEXT_ALIGN_RIGHT, 0);
    lbl_clock = label(scr, &font_mono_11, C_INACTIVE, 278, 4, 36);
    lv_obj_set_style_text_align(lbl_clock, LV_TEXT_ALIGN_RIGHT, 0);
    strip = rect(scr, 0, 21, LCD_H_RES, 3, C_SUBTLE);

    body = lv_obj_create(scr);
    lv_obj_remove_style_all(body);
    lv_obj_set_pos(body, 0, Y_BODY);
    lv_obj_set_size(body, LCD_H_RES, LCD_V_RES - Y_BODY);
    lv_obj_remove_flag(body, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);

    clawd_create(body, 6, 3);

    lbl_spin = label(body, &font_mono_16b, C_CLAUDE, X_RIGHT, 8, 0);
    lbl_verb = label(body, &font_mono_16b, C_SHIMMER, X_RIGHT + 20, 8, W_RIGHT - 20);
    lbl_tb = label(body, &font_mono_13, C_SUCCESS, X_RIGHT, 34, 0);
    lv_label_set_text_static(lbl_tb, G_BULLET);
    lbl_tt = label(body, &font_mono_13, C_TEXT, X_RIGHT + 14, 34, W_RIGHT - 14);
    lbl_meta = label(body, &font_mono_13, C_INACTIVE, X_RIGHT + 14, 54, W_RIGHT - 14);
    lbl_big = label(body, &font_clock_44b, C_TEXT, X_RIGHT, 2, W_RIGHT);
    lbl_date = label(body, &font_mono_13, C_INACTIVE, X_RIGHT + 2, 54, W_RIGHT - 2);
    show(lbl_big, false);
    show(lbl_date, false);

    static const char *names[G_N] = {"5h", "week", "ctx"};
    static const char *units[G_N] = {"left", "left", "used"};
    for (int i = 0; i < G_N; i++) {
        gauge_t *g = &gauges[i];
        int y = Y_GAUGE + i * GAUGE_PITCH;
        g->name = label(body, &font_mono_13, C_INACTIVE, 8, y + 2, 0);
        lv_label_set_text_static(g->name, names[i]);
        g->bar = bar(body, 48, y + 7, 120, 8);
        g->val = label(body, &font_mono_16b, C_TEXT, 172, y, 48);
        lv_obj_set_style_text_align(g->val, LV_TEXT_ALIGN_RIGHT, 0);
        g->unit = label(body, &font_mono_13, C_INACTIVE, 225, y + 2, 34);
        lv_label_set_text(g->unit, units[i]);  // not static: LONG_DOT may rewrite the buffer
        // ctx has no reset countdown; its slot spans the unit too, for "→ compact" (D48).
        g->reset = i == G_CTX ? label(body, &font_mono_13, C_INACTIVE, 225, y + 2, 89)
                              : label(body, &font_mono_13, C_INACTIVE, 262, y + 2, 52);
        lv_obj_set_style_text_align(g->reset, LV_TEXT_ALIGN_RIGHT, 0);
    }
    lbl_today = label(body, &font_mono_13, C_INACTIVE, 8, Y_GAUGE + G_CTX * GAUGE_PITCH + 2, 304);
    show(lbl_today, false);

    banner = rect(body, 0, Y_BANNER - Y_BODY, LCD_H_RES, LCD_V_RES - Y_BANNER, C_CLAUDE);
    lbl_b1 = label(banner, &font_mono_20b, C_BG, 10, 9, 300);
    lbl_b2 = label(banner, &font_mono_13, C_BG, 10, 38, 300);
    show(banner, false);
    overview_create();

    lv_timer_create(spin_cb, 110, NULL);
    lv_timer_create(slow_cb, 250, NULL);
    load_brightness();
    render();
}

void ui_on_message(const cJSON *msg, proto_src_t src)
{
    const cJSON *t = cJSON_GetObjectItemCaseSensitive(msg, "t");
    const cJSON *tm = cJSON_GetObjectItemCaseSensitive(msg, "tm");
    const cJSON *tz = cJSON_GetObjectItemCaseSensitive(msg, "tz");
    if (cJSON_IsNumber(tm) && cJSON_IsNumber(tz)) {  // hb and snap carry the Mac's clock (D46)
        s_epoch_off_s = (int64_t)tm->valuedouble - esp_timer_get_time() / 1000000;
        s_tz_s = (int32_t)tz->valuedouble;
        s_time_known = true;
    }
    if (!cJSON_IsString(t) || strcmp(t->valuestring, "snap") != 0) {
        return;  // heartbeats otherwise only refresh proto_last_rx_us()
    }
    int64_t t0 = esp_timer_get_time();
    snap_t n;
    snap_parse(msg, &n);
    // D47: a plan window dropping under 15 % left gets one amber pulse (not on the first snapshot).
    if (s_have_snap) {
        const int16_t was[2] = {s_snap.h5, s_snap.d7}, now[2] = {n.h5, n.d7};
        for (int i = 0; i < 2; i++) {
            if (was[i] >= 0 && now[i] >= 0 && 100 - was[i] >= LOW_LEFT_PCT && 100 - now[i] < LOW_LEFT_PCT) {
                led_pulse(C_WARNING, 3);
            }
        }
    }
    sess_state_t old_st = s_have_snap && s_snap.has_focus ? s_snap.st : ST_NONE;
    bool switched = s_have_snap && s_snap.has_focus && n.has_focus && strcmp(s_snap.project, n.project) != 0;
    s_snap = n;
    s_have_snap = true;
    s_snap_rx_us = t0;
    if (!n.has_focus || n.st != old_st) {
        s_state_since_us = t0;
    }
    s_offline = false;
    ota_mark_valid();  // D37: a working bridge link proves this image
    render();
    if (switched) {
        slide_in();
    }
    if (overview_open()) {
        render_overview();
    }
    lv_refr_now(NULL);

    const cJSON *seq = cJSON_GetObjectItemCaseSensitive(msg, "seq");
    if (cJSON_IsNumber(seq)) {
        char buf[64];
        int len = snprintf(buf, sizeof(buf), "{\"t\":\"ack\",\"seq\":%ld,\"us\":%lld}\n", (long)seq->valuedouble,
                           esp_timer_get_time() - t0);
        proto_reply(src, buf, (size_t)len);
    }
}

void ui_on_button(bool long_press)
{
    if (long_press) {
        s_bl_low = !s_bl_low;
        save_brightness();
        render_power();
        return;
    }
    // Short press: the host owns focus, so ask it to move to the next session. The list of
    // sessions shows meanwhile; the highlight moves when the host's snapshot arrives (D51).
    static const char msg[] = "{\"t\":\"btn\",\"e\":\"short\"}\n";
    proto_send_all(msg, sizeof(msg) - 1);
    s_overview_until_us = esp_timer_get_time() + OVERVIEW_US;
    render_overview();
    show(ov, true);
}
