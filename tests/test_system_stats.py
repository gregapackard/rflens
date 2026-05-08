import unittest

from backend.system_stats import read_meminfo_memory


class FakeMeminfo:
    def __init__(self, text: str):
        self.text = text

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.text


class SystemStatsTests(unittest.TestCase):
    def test_read_meminfo_memory_calculates_usage_percent(self):
        memory = read_meminfo_memory(FakeMeminfo("\n".join([
            "MemTotal:        8000000 kB",
            "MemFree:         1000000 kB",
            "MemAvailable:    2000000 kB",
            "Buffers:          100000 kB",
        ])))

        self.assertIsNotNone(memory)
        assert memory is not None
        self.assertEqual(memory["total_mb"], 7812.5)
        self.assertEqual(memory["available_mb"], 1953.1)
        self.assertEqual(memory["used_mb"], 5859.4)
        self.assertEqual(memory["percent"], 75.0)

    def test_read_meminfo_memory_returns_none_without_required_fields(self):
        self.assertIsNone(read_meminfo_memory(FakeMeminfo("MemTotal: 8000000 kB\n")))


if __name__ == "__main__":
    unittest.main()
