import radar_core as rc
from fakes import MB, P, meneer_snapshot


def test_preserve_selection_keeps_order_and_drops_exited():
    assert rc.preserve_selection([5, 3, 9], [1, 3, 5, 7]) == [5, 3]
    assert rc.preserve_selection([], [1, 2]) == []
    assert rc.preserve_selection([4, 4, 2], [2, 4]) == [4, 2]
    # works with (pid, create_time) keys: a reused PID with a new start time is NOT kept
    assert rc.preserve_selection([(10, 1.0), (11, 1.0)], [(10, 1.0), (11, 2.0)]) == [(10, 1.0)]


def test_stop_plan_excludes_self_and_flags_system_and_shadow():
    snap = meneer_snapshot()
    plan = rc.stop_plan([8304, 9196, 1316, 10336, 8304, 424242], snap.procs, self_pid=9196)
    assert plan.pids == [8304, 1316, 10336]
    assert plan.excluded_self and plan.missing == [424242]
    warn = {i.pid: i.warning for i in plan.items}
    assert warn == {8304: "", 1316: "Windows system process", 10336: "Shadow PC service"}
    text = " ".join(plan.warnings)
    assert "Process Radar itself" in text and "Windows system" in text and "Shadow" in text and "already exited" in text
    assert plan.total_rss == sum(snap.procs[p].rss for p in plan.pids)


def test_stop_plan_children_and_kill_tree_default():
    snap = meneer_snapshot()
    plan = rc.stop_plan([8304, 30000], snap.procs)
    kids = {i.pid: i.children for i in plan.items}
    assert kids == {8304: [7564], 30000: []} and plan.any_children
    # a child that is itself selected is not listed twice
    plan = rc.stop_plan([8184, 8304], snap.procs)
    kids = {i.pid: sorted(i.children) for i in plan.items}
    assert kids[8184] == [5848, 7564] and kids[8304] == [7564]
    assert not rc.stop_plan([30000, 30001], snap.procs).any_children
    labels = {i.pid: i.label for i in rc.stop_plan([8304, 14672], snap.procs).items}
    assert labels == {8304: "ComfyUI", 14672: "Ollama"}


def test_stop_plan_only_self():
    snap = meneer_snapshot()
    plan = rc.stop_plan([30000], snap.procs, self_pid=30000)
    assert plan.items == [] and plan.excluded_self


def test_stop_summary():
    text, counts = rc.stop_summary({11: "stopped", 12: "gone", 13: "denied", 14: "stopped", 15: "alive"},
                                   {11: "python.exe", 13: "ShadowManager.exe"})
    assert counts == {"stopped": 2, "gone": 1, "denied": 1, "alive": 1, "refused": 0}
    head, *lines = text.splitlines()
    assert head == "2 stopped, 1 already gone, 1 access denied, 1 still running"
    assert lines[0].split()[:3] == ["11", "python.exe", "stopped"]
    assert "ShadowManager.exe" in lines[3] and "access denied" in lines[3]
    assert rc.stop_summary({})[0] == "nothing to do"


def test_is_shadow_ignores_user_folder_named_shadow():
    assert rc.is_shadow(P(1, "ShadowManager.exe", exe=r"C:\Program Files\Blade Group\ShadowManager\ShadowManager.exe"))
    assert rc.is_shadow(P(1, "x.exe", exe=r"C:\Program Files\Shadow\x\x.exe"))
    assert not rc.is_shadow(P(1, "python.exe", exe=r"C:\Users\Shadow\Documents\ComfyUI\python.exe"))
