"""The one property picker every PMS adapter shares (--property by id or name)."""

from __future__ import annotations

import unittest

from _match import loose, pick

ROWS = [
    {"id": "p1", "name": "The Après Arcade"},
    {"id": "p2", "name": "Boho Bliss"},
    {"id": "p3", "name": "The Lakehouse"},
    {"id": "p4", "name": "Lakehouse North"},
]


class Boom(Exception):
    pass


def choose(selector, rows=ROWS, **kw):
    return pick(rows, selector, message="must match exactly one", error=Boom, **kw)


class Pick(unittest.TestCase):
    def test_id_then_exact_name(self):
        self.assertEqual(choose("p2")["name"], "Boho Bliss")
        self.assertEqual(choose("boho bliss")["id"], "p2")

    def test_loose_name_drops_accents_case_and_a_leading_the(self):
        self.assertEqual(loose("The  Après-Arcade!"), "apres arcade")
        self.assertEqual(choose("apres arcade")["id"], "p1")
        self.assertEqual(choose("Apres Arcade")["id"], "p1")

    def test_an_exact_tie_is_never_broken_by_a_loose_match(self):
        rows = ROWS + [{"id": "p5", "name": "Boho Bliss"}]
        with self.assertRaisesRegex(Boom, "2 match 'Boho Bliss'"):
            choose("Boho Bliss", rows)

    def test_a_loose_tie_refuses_and_names_both(self):
        rows = [{"id": "a", "name": "The Loft"}, {"id": "b", "name": "Loft"}]
        with self.assertRaisesRegex(Boom, "2 match 'loft!'.*Loft, The Loft"):
            choose("loft!", rows)

    def test_no_match_names_the_closest_properties(self):
        with self.assertRaisesRegex(Boom, "closest: The Lakehouse"):
            choose("Lake house")

    def test_no_match_and_nothing_close_is_the_plain_message(self):
        with self.assertRaises(Boom) as ctx:
            choose("Zzzz Qqqq")
        self.assertEqual(str(ctx.exception), "must match exactly one")

    def test_a_name_with_no_latin_letters_never_matches_another_empty_key(self):
        rows = [{"id": "jp", "name": "東京の家"}, {"id": "p2", "name": "Boho Bliss"}]
        self.assertEqual(loose("東京の家"), "")
        for selector in ("!!!", "the", "   "):
            with self.assertRaises(Boom):
                choose(selector, rows)
        self.assertEqual(choose("東京の家", rows)["id"], "jp")  # exact still works
        rows.append({"id": "none", "name": None})
        with self.assertRaisesRegex(Boom, "2 match 'boho bliss'"):
            choose("boho bliss", rows + [{"id": "p9", "name": "BOHO BLISS"}])

    def test_extra_name_fields_count(self):
        rows = [{"id": "u1", "name": "Unit 4B", "public_name": "Sunset Loft"}]
        self.assertEqual(choose("sunset loft", rows, names=lambda r: (r["name"], r["public_name"]))["id"], "u1")


if __name__ == "__main__":
    unittest.main()
