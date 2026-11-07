from tests.test_jobs import make_cfg
from mao_cli import menu as m


def run(keys, cfg, tmp_path, tex, with_exports=True):
    state = m.build_state(cfg, tmp_path, tex, with_exports=with_exports)
    seq, frames = iter(keys), []
    result = m.run_menu(state, title="T", read=lambda: next(seq), write=frames.append, size=lambda: (30, 100))
    return result, frames


def test_menu_starts_with_nothing_selected_and_enter_needs_a_choice(tmp_path, tex):
    result, frames = run(["enter", "space", "enter"], make_cfg(), tmp_path, tex)
    assert result.chosen_jobs == {"teil"} and not result.chosen_exports
    assert "Nichts ausgewählt" in frames[1]
    assert "[ ] teil" in frames[0] and "[ ] raum" in frames[0]


def test_menu_arrows_toggle_all_none_and_quit(tmp_path, tex):
    result, _ = run(["down", "space", "down", "space", "up", "space", "enter"], make_cfg(), tmp_path, tex)
    assert result.chosen_jobs == {"fest"}  # raum an, fest an, raum wieder aus
    result, _ = run(["a", "n", "a", "enter"], make_cfg(), tmp_path, tex)
    assert result.chosen_jobs == {"teil", "raum", "fest"}
    assert run(["q"], make_cfg(), tmp_path, tex)[0] is None
    assert run(["esc"], make_cfg(), tmp_path, tex)[0] is None


def test_menu_shows_derived_exports_and_group_commands_for_exports(tmp_path, tex):
    result, frames = run(["down", "space", "down", "down", "space", "a", "n", "end", "space", "enter"],
                         make_cfg(), tmp_path, tex)
    assert result.chosen_jobs == {"raum"} and result.chosen_exports == {"rangliste"}
    assert "[+] raeume" in frames[3]  # durch Job 'raum' automatisch
    assert "Gewählt: 1 Job(s) · 1 Export(e)" in frames[3]


def test_menu_for_latex_has_no_export_group(tmp_path, tex):
    result, frames = run(["space", "enter"], make_cfg(), tmp_path, tex, with_exports=False)
    assert result.chosen_jobs == {"teil"} and "Exporte (nur Daten" not in frames[0]


def test_menu_scrolls_with_many_rows(tmp_path, tex):
    cfg = make_cfg()
    cfg["latex"]["jobs"] += [{"name": f"j{n}", "tex": "@tex/Fest/Fest.tex", "exports": []} for n in range(40)]
    state = m.build_state(cfg, tmp_path, tex, with_exports=True)
    state.cursor = len(state.rows()) - 1
    frame = m.render(state, 15, 100, "T")
    assert "\\r\\n" not in frame and frame.count("\r\n") <= 15
    assert "> [ ] rangliste" in frame.replace("\x1b[7m", "").replace("\x1b[0m", "")
