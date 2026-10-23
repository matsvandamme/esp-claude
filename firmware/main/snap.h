#pragma once
// Snapshot of what the screen should show, decoded from the host's {"t":"snap"} message.
// The host owns all state; the device only runs local timers (elapsed, waiting) from the
// baselines given here, so the host can stay quiet between changes.
#include <stdbool.h>
#include <stdint.h>

#include "cJSON.h"

typedef enum {
    ST_NONE = 0,
    ST_START,
    ST_THINK,
    ST_TOOL,
    ST_PERM,
    ST_ASK,
    ST_DONE,
    ST_IDLE,
    ST_ERROR,
    ST_COMPACT,
    ST_LIMIT,  // a plan usage limit was hit (D35)
} sess_state_t;

#define SNAP_MAX_SESS 8

typedef struct {
    char name[16];
    char target[48];
    uint8_t status;  // 0 done, 1 running, 2 failed
} snap_tool_t;

// One row of the session overview (D51).
typedef struct {
    char label[28];
    sess_state_t st;
    int32_t age_s;  // seconds in this state when the snapshot was built
    int32_t color;  // /color as 0xRRGGBB, -1 none
    bool done_away;
} snap_sess_t;

typedef struct {
    bool has_focus;
    char project[28];
    char model[20];
    char verb[24];
    char reason[40];  // why it needs you / error kind
    sess_state_t st;
    int32_t elapsed_s;  // turn time at the moment the snapshot was built
    int32_t wait_s;     // seconds already waiting on the user
    int32_t color;          // /color of the session as 0xRRGGBB, -1 none (D41)
    int32_t limit_reset_s;  // ST_LIMIT: seconds until the limit lifts, -1 unknown
    int32_t tool_age_s;   // the running tool's age, -1 none (D50)
    int32_t done_away_s;  // finished while you were away this long ago, -1 not flagged (D49)
    int16_t ntools;
    int16_t nsub;
    uint8_t ntl;
    snap_tool_t tools[2];

    uint8_t nsess;
    int8_t focus_idx;
    sess_state_t sessions[SNAP_MAX_SESS];
    uint8_t nrows;
    snap_sess_t rows[SNAP_MAX_SESS];  // "ss": the overview list, same order as `sessions`

    int16_t ctx_pct;  // -1 unknown
    int32_t ctx_tok;
    int32_t ctx_win;
    int16_t h5;         // -1 unknown
    int16_t d7;         // -1 unknown
    int32_t h5_reset_s; // seconds until the 5-hour window resets, -1 unknown
    int32_t d7_reset_s; // seconds until the weekly window resets, -1 unknown

    bool has_other;           // another session is waiting for the user (badge)
    char other_project[28];
    uint8_t other_count;

    bool has_done_other;  // another session finished while you were away (D49)
    char done_other_project[28];
    uint8_t done_other_count;

    bool has_today;  // D52
    int32_t today_turns;
    int32_t today_active_s;
    int32_t today_tokens;

    bool wifi_blocked;  // the Mac can't reach the display over WiFi (macOS Local Network)
} snap_t;

sess_state_t snap_state_from_str(const char *s);
bool snap_state_needs_user(sess_state_t st);
bool snap_state_working(sess_state_t st);

// Decode `msg` into `out`. Missing fields keep sane defaults. Returns false if not a snapshot.
bool snap_parse(const cJSON *msg, snap_t *out);
