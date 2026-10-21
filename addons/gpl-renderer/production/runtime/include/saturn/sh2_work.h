// Diagnostic instruction work only; never changes the legacy clock or scheduling.
#pragma once
#include <cstdint>
#include <cstdio>

struct SH2Work {
    uint64_t native_instructions = 0, interpreted_instructions = 0;
    uint64_t native_cycles = 0, interpreted_cycles = 0;
    uint64_t accrued_cycles = 0, max_poll_cycles = 0;
    uint64_t poll_attempts = 0, expired_polls = 0;
    uint64_t legacy_poll_ns = 0, legacy_sleep_ns = 0;
    uint64_t unclassified = 0;
};

// The existing coroutine baton permits only one executing SH2 at a time.
inline SH2Work g_sh2_work[2];
inline uint64_t g_sh2_work_invalid_cpu;

template<class C> inline SH2Work* sh2_work_cpu(const C& c) {
    if (c.cpu < 0 || c.cpu > 1) { ++g_sh2_work_invalid_cpu; return nullptr; }
    return &g_sh2_work[c.cpu];
}

template<class C> inline void sh2_work_instruction(const C& c, uint32_t cycles, bool native) {
    SH2Work* w = sh2_work_cpu(c);
    if (!w) return;
    if (native) { ++w->native_instructions; w->native_cycles += cycles; }
    else { ++w->interpreted_instructions; w->interpreted_cycles += cycles; }
    if (!cycles) ++w->unclassified;
    w->accrued_cycles += cycles;
    if (w->accrued_cycles > w->max_poll_cycles) w->max_poll_cycles = w->accrued_cycles;
}

#define SH2_WORK_NATIVE(c, cost) sh2_work_instruction((c), (cost), true)
template<class C> inline void sh2_work_interpreted(const C& c, uint32_t cycles) {
    sh2_work_instruction(c, cycles, false);
}
template<class C> inline void sh2_work_poll(const C& c, bool expired) {
    SH2Work* w = sh2_work_cpu(c);
    if (!w) return;
    ++w->poll_attempts;
    if (expired) ++w->expired_polls;
    w->accrued_cycles = 0;
}
template<class C> inline void sh2_work_legacy_time(const C& c, uint64_t ns, bool sleep = false) {
    SH2Work* w = sh2_work_cpu(c);
    if (!w) return;
    if (sleep) w->legacy_sleep_ns += ns;
    else w->legacy_poll_ns += ns;
}
inline void sh2_work_reset() {
    for (SH2Work& w : g_sh2_work) w = {};
    g_sh2_work_invalid_cpu = 0;
}
inline void sh2_work_report(FILE* f) {
    for (int cpu = 0; cpu < 2; ++cpu) {
        const SH2Work& w = g_sh2_work[cpu];
        std::fprintf(f, "sh2-work-v1 cpu=%d native_instructions=%llu interpreted_instructions=%llu native_cycles=%llu interpreted_cycles=%llu accrued_cycles=%llu max_poll_cycles=%llu poll_attempts=%llu expired_polls=%llu legacy_poll_ns=%llu legacy_sleep_ns=%llu unclassified=%llu\n",
            cpu, (unsigned long long)w.native_instructions, (unsigned long long)w.interpreted_instructions,
            (unsigned long long)w.native_cycles, (unsigned long long)w.interpreted_cycles,
            (unsigned long long)w.accrued_cycles, (unsigned long long)w.max_poll_cycles,
            (unsigned long long)w.poll_attempts, (unsigned long long)w.expired_polls,
            (unsigned long long)w.legacy_poll_ns, (unsigned long long)w.legacy_sleep_ns,
            (unsigned long long)w.unclassified);
    }
    std::fprintf(f, "sh2-work-v1 invalid_cpu=%llu\n", (unsigned long long)g_sh2_work_invalid_cpu);
}
