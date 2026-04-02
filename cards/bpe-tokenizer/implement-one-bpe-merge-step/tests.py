import unittest
import answer

class ExerciseTests(unittest.TestCase):
    def test_basic_merge(self) -> None:
        self.assertEqual(
            answer.merge_once(("l", "o", "w"), ("l", "o")),
            ("lo", "w"),
        )

    def test_multiple_occurrences(self) -> None:
        self.assertEqual(
            answer.merge_once(("a", "b", "a", "b"), ("a", "b")),
            ("ab", "ab"),
        )

    def test_no_merge(self) -> None:
        self.assertEqual(
            answer.merge_once(("a", "x", "b"), ("a", "b")),
            ("a", "x", "b"),
        )

if __name__ == "__main__":
    unittest.main()