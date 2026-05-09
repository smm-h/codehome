"""Tests for metrics calculation helpers and MetricsCollector history."""

import time

from codehome.serve.metrics import (
    _HISTORY_SIZE,
    MetricsCollector,
    _calc_cpu,
    _calc_mem_limit_mb,
    _calc_mem_mb,
    _calc_net,
)

# ---------------------------------------------------------------------------
# 1-3. _calc_cpu
# ---------------------------------------------------------------------------


class TestCalcCpu:
    def test_normal_case(self):
        stats = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 200},
                "system_cpu_usage": 2000,
                "online_cpus": 4,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 100},
                "system_cpu_usage": 1000,
            },
        }
        # cpu_delta=100, system_delta=1000, 4 cpus -> (100/1000)*4*100 = 40.0
        assert _calc_cpu(stats) == 40.0

    def test_zero_system_delta(self):
        stats = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 200},
                "system_cpu_usage": 1000,
                "online_cpus": 4,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 100},
                "system_cpu_usage": 1000,  # same -> delta=0
            },
        }
        assert _calc_cpu(stats) == 0.0

    def test_missing_keys(self):
        assert _calc_cpu({}) == 0.0
        assert _calc_cpu({"cpu_stats": {}}) == 0.0


# ---------------------------------------------------------------------------
# 4-6. _calc_mem_mb
# ---------------------------------------------------------------------------


class TestCalcMemMb:
    def test_normal_with_cache_subtraction(self):
        usage = 200 * 1024 * 1024  # 200 MB
        cache = 50 * 1024 * 1024  # 50 MB
        stats = {
            "memory_stats": {
                "usage": usage,
                "stats": {"cache": cache},
            },
        }
        assert _calc_mem_mb(stats) == 150.0

    def test_no_cache_cgroups_v2(self):
        usage = 100 * 1024 * 1024  # 100 MB
        stats = {
            "memory_stats": {
                "usage": usage,
                # No "stats" sub-dict -> cache defaults to 0.
            },
        }
        assert _calc_mem_mb(stats) == 100.0

    def test_missing_keys(self):
        assert _calc_mem_mb({}) == 0.0
        assert _calc_mem_mb({"memory_stats": {}}) == 0.0


# ---------------------------------------------------------------------------
# 7. _calc_mem_limit_mb
# ---------------------------------------------------------------------------


class TestCalcMemLimitMb:
    def test_normal_case(self):
        limit = 8 * 1024 * 1024 * 1024  # 8 GB
        stats = {"memory_stats": {"limit": limit}}
        assert _calc_mem_limit_mb(stats) == 8192.0

    def test_missing_keys(self):
        assert _calc_mem_limit_mb({}) == 0.0


# ---------------------------------------------------------------------------
# 8-9. _calc_net
# ---------------------------------------------------------------------------


class TestCalcNet:
    def test_multiple_interfaces_summed(self):
        stats = {
            "networks": {
                "eth0": {"rx_bytes": 1000, "tx_bytes": 2000},
                "eth1": {"rx_bytes": 500, "tx_bytes": 300},
            },
        }
        assert _calc_net(stats, "rx_bytes") == 1500
        assert _calc_net(stats, "tx_bytes") == 2300

    def test_no_networks(self):
        assert _calc_net({}, "rx_bytes") == 0
        assert _calc_net({"networks": {}}, "rx_bytes") == 0


# ---------------------------------------------------------------------------
# 10. History buffer max size
# ---------------------------------------------------------------------------


class TestHistoryBuffer:
    def test_max_size_retained(self):
        mc = MetricsCollector()
        for i in range(200):
            mc.history.append({"ts": float(i), "containers": []})
        assert len(mc.history) == _HISTORY_SIZE  # 150


# ---------------------------------------------------------------------------
# 11. get_history time filtering
# ---------------------------------------------------------------------------


class TestGetHistory:
    def test_filters_by_minutes(self):
        mc = MetricsCollector()
        now = time.time()
        # Insert entries spanning 10 minutes ago to now, one per minute.
        for m in range(10):
            mc.history.append({"ts": now - (m * 60), "containers": []})
        # Ask for last 5 minutes: should get entries 0..4 (now through 4 min ago).
        result = mc.get_history(minutes=5)
        assert len(result) == 5
        # All returned entries should be within the last 5 minutes.
        cutoff = now - 5 * 60
        assert all(e["ts"] >= cutoff for e in result)
