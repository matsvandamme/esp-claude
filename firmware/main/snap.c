#include "snap.h"

#include <string.h>

static const struct {
    const char *name;
    sess_state_t st;
} STATES[] = {
    {"start", ST_START}, {"think", ST_THINK}, {"tool", ST_TOOL}, {"perm", ST_PERM},     {"ask", ST_ASK},
    {"done", ST_DONE},   {"idle", ST_IDLE},   {"error", ST_ERROR}, {"compact", ST_COMPACT}, {"limit", ST_LIMIT},
};

sess_state_t snap_state_from_str(const char *s)
{
    if (!s) {
        return ST_NONE;
    }
    for (size_t i = 0; i < sizeof(STATES) / sizeof(STATES[0]); i++) {
        if (strcmp(s, STATES[i].name) == 0) {
            return STATES[i].st;
        }
    }
    return ST_NONE;
}

bool snap_state_needs_user(sess_state_t st)
{
    return st == ST_PERM || st == ST_ASK;
}

bool snap_state_working(sess_state_t st)
{
    return st == ST_START || st == ST_THINK || st == ST_TOOL || st == ST_COMPACT;
}

static void copy_str(char *dst, size_t cap, const cJSON *obj, const char *key)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(obj, key);
    if (cJSON_IsString(v) && v->valuestring) {
        strlcpy(dst, v->valuestring, cap);
    } else {
        dst[0] = 0;
    }
}

static int32_t get_int(const cJSON *obj, const char *key, int32_t dflt)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsNumber(v) ? (int32_t)v->valuedouble : dflt;
}

bool snap_parse(const cJSON *msg, snap_t *out)
{
    memset(out, 0, sizeof(*out));
    out->focus_idx = -1;
    out->ctx_pct = -1;
    out->h5 = -1;
    out->d7 = -1;
    out->h5_reset_s = -1;
    out->d7_reset_s = -1;
    out->color = -1;
    out->limit_reset_s = -1;
    out->tool_age_s = -1;
    out->done_away_s = -1;

    const cJSON *f = cJSON_GetObjectItemCaseSensitive(msg, "f");
    if (cJSON_IsObject(f)) {
        out->has_focus = true;
        copy_str(out->project, sizeof(out->project), f, "p");
        copy_str(out->model, sizeof(out->model), f, "m");
        copy_str(out->verb, sizeof(out->verb), f, "v");
        copy_str(out->reason, sizeof(out->reason), f, "w");
        const cJSON *st = cJSON_GetObjectItemCaseSensitive(f, "st");
        out->st = snap_state_from_str(cJSON_IsString(st) ? st->valuestring : NULL);
        out->elapsed_s = get_int(f, "el", 0);
        out->wait_s = get_int(f, "ws", 0);
        out->limit_reset_s = get_int(f, "lr", -1);
        out->color = get_int(f, "c", -1);
        out->tool_age_s = get_int(f, "ta", -1);
        out->done_away_s = get_int(f, "da", -1);
        out->ntools = (int16_t)get_int(f, "nt", 0);
        out->nsub = (int16_t)get_int(f, "sub", 0);
        const cJSON *tl = cJSON_GetObjectItemCaseSensitive(f, "tl");
        const cJSON *t;
        cJSON_ArrayForEach(t, tl)
        {
            if (out->ntl >= 2 || !cJSON_IsArray(t)) {
                break;
            }
            snap_tool_t *dst = &out->tools[out->ntl++];
            const cJSON *n = cJSON_GetArrayItem(t, 0), *g = cJSON_GetArrayItem(t, 1), *s = cJSON_GetArrayItem(t, 2);
            strlcpy(dst->name, cJSON_IsString(n) ? n->valuestring : "", sizeof(dst->name));
            strlcpy(dst->target, cJSON_IsString(g) ? g->valuestring : "", sizeof(dst->target));
            dst->status = cJSON_IsNumber(s) ? (uint8_t)s->valueint : 0;
        }
    }

    const cJSON *s = cJSON_GetObjectItemCaseSensitive(msg, "s");
    const cJSON *e;
    cJSON_ArrayForEach(e, s)
    {
        if (out->nsess >= SNAP_MAX_SESS) {
            break;
        }
        out->sessions[out->nsess++] = snap_state_from_str(cJSON_IsString(e) ? e->valuestring : NULL);
    }
    out->focus_idx = (int8_t)get_int(msg, "fi", out->has_focus ? 0 : -1);
    const cJSON *ss = cJSON_GetObjectItemCaseSensitive(msg, "ss");
    cJSON_ArrayForEach(e, ss)
    {
        if (out->nrows >= SNAP_MAX_SESS || !cJSON_IsArray(e)) {
            break;
        }
        snap_sess_t *r = &out->rows[out->nrows++];
        const cJSON *l = cJSON_GetArrayItem(e, 0), *st = cJSON_GetArrayItem(e, 1), *age = cJSON_GetArrayItem(e, 2);
        const cJSON *c = cJSON_GetArrayItem(e, 3), *fl = cJSON_GetArrayItem(e, 4);
        strlcpy(r->label, cJSON_IsString(l) ? l->valuestring : "", sizeof(r->label));
        r->st = snap_state_from_str(cJSON_IsString(st) ? st->valuestring : NULL);
        r->age_s = cJSON_IsNumber(age) ? (int32_t)age->valuedouble : 0;
        r->color = cJSON_IsNumber(c) ? (int32_t)c->valuedouble : -1;
        r->done_away = cJSON_IsNumber(fl) && ((int)fl->valuedouble & 1);
    }

    const cJSON *m = cJSON_GetObjectItemCaseSensitive(msg, "m");
    if (cJSON_IsObject(m)) {
        out->ctx_pct = (int16_t)get_int(m, "ctx", -1);
        out->ctx_tok = get_int(m, "ctk", 0);
        out->ctx_win = get_int(m, "cw", 0);
        out->h5 = (int16_t)get_int(m, "h5", -1);
        out->d7 = (int16_t)get_int(m, "d7", -1);
        out->h5_reset_s = get_int(m, "h5r", -1);
        out->d7_reset_s = get_int(m, "d7r", -1);
    }
    const cJSON *ob = cJSON_GetObjectItemCaseSensitive(msg, "ob");
    if (cJSON_IsObject(ob)) {
        out->has_other = true;
        copy_str(out->other_project, sizeof(out->other_project), ob, "p");
        out->other_count = (uint8_t)get_int(ob, "n", 1);
    }
    const cJSON *od = cJSON_GetObjectItemCaseSensitive(msg, "od");
    if (cJSON_IsObject(od)) {
        out->has_done_other = true;
        copy_str(out->done_other_project, sizeof(out->done_other_project), od, "p");
        out->done_other_count = (uint8_t)get_int(od, "n", 1);
    }
    const cJSON *td = cJSON_GetObjectItemCaseSensitive(msg, "td");
    if (cJSON_IsObject(td)) {
        out->has_today = true;
        out->today_turns = get_int(td, "n", 0);
        out->today_active_s = get_int(td, "a", 0);
        out->today_tokens = get_int(td, "k", 0);
    }
    out->wifi_blocked = get_int(msg, "wb", 0) != 0;
    return true;
}
