#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"
APP = ROOT.parent / "apps/testing/p2schedstress"


class SchedulerStressSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (APP / "p2schedstress_main.c").read_text()
        cls.kconfig = (APP / "Kconfig").read_text()
        cls.profile = (BOARD / "configs/schedstress/defconfig").read_text()

    def test_accounted_event_budget_exceeds_one_million(self):
        names = (
            "PRIORITY",
            "RR",
            "SEMAPHORE",
            "PI",
            "CONDITION",
            "MQUEUE",
            "SIGNAL",
            "TIMER",
            "PTHREAD",
            "TASK",
        )
        counts = {}
        for name in names:
            match = re.search(
                rf"#define P2SCHED_{name}_EVENTS\s+(\d+)u", self.source
            )
            self.assertIsNotNone(match, name)
            counts[name] = int(match.group(1))

        self.assertEqual(sum(counts.values()), 1_004_078)
        self.assertGreaterEqual(sum(counts.values()), 1_000_000)
        self.assertIn("total != P2SCHED_TOTAL_EVENTS", self.source)
        self.assertIn("total < 1000000u", self.source)
        self.assertIn("P2SCHED:PROFILE:MODE=FLAT-UP:RAM=%u", self.source)
        self.assertIn("#ifdef CONFIG_SMP", self.source)
        self.assertIn("P2SCHED_HEAP_CONCURRENCY_EVENTS != 512u", self.source)

    def test_every_required_mechanism_has_real_api_evidence(self):
        for token in (
            "sched_yield();",
            "sem_wait(sem);",
            "sem_post(&g_sem_baton",
            "pthread_mutexattr_setprotocol",
            "PTHREAD_PRIO_INHERIT",
            "pthread_cond_wait",
            "pthread_cond_signal",
            "mq_send",
            "mq_receive",
            "pthread_kill",
            "sigwaitinfo",
            "timer_create",
            "timer_settime",
            "pthread_create",
            "pthread_join",
            "pthread_cancel",
            "task_create",
            "waitpid",
            "up_check_tcbstack",
            "mallinfo()",
        ):
            self.assertIn(token, self.source)

    def test_terminal_marker_follows_all_exact_stage_checks(self):
        stages = (
            "PRIORITY",
            "ROUNDROBIN",
            "SEMAPHORE",
            "PI_MUTEX",
            "CONDITION",
            "MQUEUE",
            "SIGNAL",
            "TIMER",
            "PTHREAD",
            "TASK_RECREATE",
        )
        positions = []
        for stage in stages:
            marker = f'P2SCHED:{stage}:PASS:COUNT=%'
            self.assertIn(marker, self.source)
            positions.append(self.source.index(marker))

        self.assertEqual(positions, sorted(positions))
        total_check = self.source.index("total != P2SCHED_TOTAL_EVENTS")
        heap = self.source.index("P2SCHED:HEAP:PASS:CHECKS=5:")
        heap_concurrency_start = self.source.index(
            "P2SCHED:HEAP_CONCURRENCY:START:"
        )
        heap_concurrency_pass = self.source.index(
            "P2SCHED:HEAP_CONCURRENCY:PASS:COUNT=512"
        )
        total_marker = self.source.index("P2SCHED:TOTAL:PASS:COUNT=%")
        terminal = self.source.index("P2SCHED:PASS:COUNT=%")
        self.assertLess(max(positions), total_check)
        self.assertLess(heap, heap_concurrency_start)
        self.assertLess(heap_concurrency_start, heap_concurrency_pass)
        self.assertLess(heap_concurrency_pass, total_check)
        self.assertLess(total_check, total_marker)
        self.assertLess(total_marker, terminal)
        self.assertIn('P2SCHED:FAIL:%s:CODE=%d', self.source)

    def test_event_counters_are_tied_to_completed_operations(self):
        required_pairs = (
            ("g_priority_post_returned != 0", "g_priority_count++;"),
            ("while (g_rr_turn != id)", "g_rr_count[id]++;"),
            ("p2sched_wait(&g_sem_baton[id])", "g_sem_count[id]++;"),
            ("pthread_mutex_lock(&g_pi_mutex)", "g_pi_count++;"),
            ("pthread_cond_wait", "g_condition_count++;"),
            ("mq_receive", "g_mqueue_receive_count++;"),
            ("sigwaitinfo", "g_signal_receive_count++;"),
            ("WEXITSTATUS(status)", "count++;"),
        )
        for operation, counter in required_pairs:
            self.assertIn(operation, self.source)
            self.assertIn(counter, self.source)

    def test_heap_concurrency_keeps_both_allocations_live_each_round(self):
        self.assertIn("P2SCHED_HEAP_CONCURRENCY_THREADS 2u", self.source)
        self.assertIn("P2SCHED_HEAP_CONCURRENCY_ROUNDS  256u", self.source)
        worker = self.source[
            self.source.index("static FAR void *p2sched_heap_concurrency_worker") :
            self.source.index("static int p2sched_test_heap_concurrency")
        ]
        self.assertIn("memory = malloc(P2SCHED_HEAP_CONCURRENCY_BYTES);", worker)
        self.assertIn("peer = g_heap_concurrency_memory[other];", worker)
        self.assertEqual(worker.count("p2sched_heap_concurrency_wait();"), 3)
        self.assertLess(worker.index("peer ="), worker.rindex("free(memory);"))
        self.assertIn("g_heap_concurrency_count[id]++;", worker)

        main = self.source[self.source.index("int main(") :]
        start = main.index("P2SCHED:HEAP_CONCURRENCY:START:")
        end = main.index("P2SCHED:TOTAL:PASS:COUNT=%")
        concurrency = main[start:end]
        self.assertNotIn("total +=", concurrency)

    def test_profile_locks_flat_up_runtime_requirements(self):
        for setting in (
            'CONFIG_BUILD_FLAT=y',
            'CONFIG_INIT_ENTRYPOINT="p2schedstress_main"',
            'CONFIG_INIT_PRIORITY=100',
            'CONFIG_RAM_SIZE=524288',
            'CONFIG_RR_INTERVAL=10',
            'CONFIG_PRIORITY_INHERITANCE=y',
            'CONFIG_CANCELLATION_POINTS=y',
            'CONFIG_ENABLE_ALL_SIGNALS=y',
            'CONFIG_PREALLOC_MQ_MSGS=4',
            'CONFIG_PREALLOC_TIMERS=2',
            'CONFIG_SCHED_CHILD_STATUS=y',
            'CONFIG_SCHED_WAITPID=y',
            'CONFIG_STACK_COLORATION=y',
            'CONFIG_STACK_USAGE=y',
            'CONFIG_TESTING_P2SCHEDSTRESS=y',
        ):
            self.assertIn(setting, self.profile)

    def test_kconfig_fails_closed_without_required_kernel_features(self):
        for dependency in (
            "!DISABLE_PTHREAD && !DISABLE_MQUEUE",
            "!DISABLE_POSIX_TIMERS && ENABLE_ALL_SIGNALS",
            "CANCELLATION_POINTS && PRIORITY_INHERITANCE",
            "ARCH_HAVE_STACKCHECK && STACK_COLORATION && STACK_USAGE",
            "SCHED_HAVE_PARENT && SCHED_CHILD_STATUS && SCHED_WAITPID",
            "RR_INTERVAL > 0",
        ):
            self.assertIn(dependency, self.kconfig)


if __name__ == "__main__":
    unittest.main()
