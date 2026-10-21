// Coarse estimated-work clock; the cooperative SH2 baton serializes these fields.
#pragma once
#include "saturn/sh2_work.h"
#include <cstdint>
#include <cstdio>

inline constexpr uint64_t SH2_TIME_HZ = 28636000;
inline constexpr uint64_t SH2_TIME_GRANT = 2932;

struct SH2Time {
    uint64_t work_cursor = 0, ns = 0, remainder = 0, grant_work = 0;
    uint64_t wait_ns = 0, grant_overruns = 0, max_grant_overrun = 0;
    bool runnable = false;
};

// Core-only semantic fixtures do not enable the machine clock.
inline bool g_sh2_time_enabled = false;
inline SH2Time g_sh2_time[2];
inline uint64_t g_sh2_time_invalid;

inline SH2Time* sh2_time_cpu(int cpu) {
    if (cpu < 0 || cpu > 1) { ++g_sh2_time_invalid; return nullptr; }
    return &g_sh2_time[cpu];
}
inline uint64_t sh2_time_work(int cpu) {
    return g_sh2_work[cpu].native_cycles + g_sh2_work[cpu].interpreted_cycles;
}
inline void sh2_time_advance(SH2Time& t, uint64_t cycles) {
    const uint64_t scaled = (cycles % SH2_TIME_HZ) * 1000000000ull + t.remainder;
    t.ns += (cycles / SH2_TIME_HZ) * 1000000000ull + scaled / SH2_TIME_HZ;
    t.remainder = scaled % SH2_TIME_HZ;
}
inline void sh2_time_reset(bool enabled, uint64_t ns = 0) {
    g_sh2_time_enabled = enabled;
    g_sh2_time_invalid = 0;
    for (int cpu = 0; cpu < 2; ++cpu) {
        g_sh2_time[cpu] = {};
        g_sh2_time[cpu].work_cursor = sh2_time_work(cpu);
        g_sh2_time[cpu].ns = ns;
        g_sh2_time[cpu].grant_work = sh2_time_work(cpu) + SH2_TIME_GRANT;
    }
    g_sh2_time[0].runnable = true;
}
inline void sh2_time_consume(int cpu) {
    SH2Time* t = sh2_time_cpu(cpu);
    if (!t) return;
    const uint64_t work = sh2_time_work(cpu);
    if (work < t->work_cursor) { ++g_sh2_time_invalid; return; }
    sh2_time_advance(*t, work - t->work_cursor);
    t->work_cursor = work;
}
inline uint64_t sh2_time_position(int cpu) {
    SH2Time* t = sh2_time_cpu(cpu);
    return t ? t->ns : 0;
}
template<class C> inline bool sh2_time_expired(const C& c) {
    SH2Time* t = sh2_time_cpu(c.cpu);
    return !t || sh2_time_work(c.cpu) >= t->grant_work;
}
inline void sh2_time_grant(int cpu) {
    SH2Time* t = sh2_time_cpu(cpu);
    if (!t) return;
    const uint64_t work = sh2_time_work(cpu);
    if (work > t->grant_work) {
        const uint64_t overrun = work - t->grant_work;
        ++t->grant_overruns;
        if (overrun > t->max_grant_overrun) t->max_grant_overrun = overrun;
    }
    if (work >= t->grant_work)
        t->grant_work += ((work - t->grant_work) / SH2_TIME_GRANT + 1) * SH2_TIME_GRANT;
}
inline void sh2_time_align(int cpu, uint64_t ns) {
    sh2_time_consume(cpu);
    SH2Time* t = sh2_time_cpu(cpu);
    if (t && t->ns < ns) { t->wait_ns += ns - t->ns; t->ns = ns; }
}
inline void sh2_time_runnable(int cpu, bool runnable) {
    if (SH2Time* t = sh2_time_cpu(cpu)) t->runnable = runnable;
}
inline void sh2_time_wait(int cpu) {
    SH2Time* t = sh2_time_cpu(cpu);
    if (!t) return;
    const uint64_t before = t->ns;
    sh2_time_advance(*t, SH2_TIME_GRANT);
    t->wait_ns += t->ns - before;
}
inline uint64_t sh2_time_frontier() {
    if (g_sh2_time[0].runnable && g_sh2_time[1].runnable)
        return g_sh2_time[0].ns < g_sh2_time[1].ns ? g_sh2_time[0].ns : g_sh2_time[1].ns;
    if (g_sh2_time[1].runnable) return g_sh2_time[1].ns;
    return g_sh2_time[0].ns;
}
inline void sh2_time_report(FILE* f) {
    for (int cpu = 0; cpu < 2; ++cpu) {
        const SH2Time& t = g_sh2_time[cpu];
        std::fprintf(f, "sh2-time-v1 cpu=%d work_cursor=%llu ns=%llu remainder=%llu grant_work=%llu runnable=%d wait_ns=%llu grant_overruns=%llu max_grant_overrun=%llu\n",
            cpu, (unsigned long long)t.work_cursor, (unsigned long long)t.ns,
            (unsigned long long)t.remainder, (unsigned long long)t.grant_work, t.runnable ? 1 : 0,
            (unsigned long long)t.wait_ns, (unsigned long long)t.grant_overruns,
            (unsigned long long)t.max_grant_overrun);
    }
    std::fprintf(f, "sh2-time-v1 enabled=%d invalid=%llu\n", g_sh2_time_enabled ? 1 : 0,
        (unsigned long long)g_sh2_time_invalid);
}
