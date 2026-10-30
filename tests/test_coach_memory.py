from coach.memory import Memory, ordinal


def mistake(ply: int) -> dict:
    return {
        "ply": ply,
        "color": "white",
        "move": "h3",
        "best": "Kd2",
        "refutation": "Nc2+",
        "cp_loss": 450,
        "severity": "blunder",
        "fen_before": "",
        "message": "",
    }


def test_streak_of_knight_forks(tmp_path):
    mem = Memory(tmp_path / "m.db")
    g1 = mem.start_game("me", "them", "white")
    mem.add_mistake(g1, mistake(10), ["pin"])
    for _ in range(3):
        g = mem.start_game("me", "them", "white")
        mem.add_mistake(g, mistake(12), ["knight_fork", "knight_fork"])
    r = mem.recurrence(g, "knight_fork")
    assert (r.streak, r.games_with, r.games) == (3, 3, 4)
    assert r.sentence() == "3rd game in a row with knight forks"
    assert mem.most_recurring(g, ["pin", "knight_fork"]).motif == "knight_fork"
    assert mem.top_weakness().motif == "knight_fork"


def test_first_occurrence_says_nothing(tmp_path):
    mem = Memory(tmp_path / "m.db")
    g = mem.start_game("me", "them", "white")
    mem.add_mistake(g, mistake(5), ["skewer"])
    assert mem.most_recurring(g, ["skewer"]) is None
    assert mem.top_weakness() is None


def test_takeback_forgets_mistakes(tmp_path):
    mem = Memory(tmp_path / "m.db")
    g = mem.start_game("me", "them", "white")
    mem.add_mistake(g, mistake(5), ["pin"])
    mem.add_mistake(g, mistake(9), ["skewer"])
    mem.forget_from(g, 9)
    assert [m["motifs"] for m in mem.mistakes(g)] == [["pin"]]


def test_ordinal():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22)] == [
        "1st",
        "2nd",
        "3rd",
        "4th",
        "11th",
        "12th",
        "13th",
        "21st",
        "22nd",
    ]
