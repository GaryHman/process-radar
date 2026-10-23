import os
import subprocess
import sys
import time

import psutil

import radar_collect as rcol
import radar_core as rc


def test_live_snapshot_sees_this_process_and_listening_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    port = s.getsockname()[1]
    try:
        col = rcol.Collector(gpu=False, ollama=False)
        col.snapshot()
        snap = col.snapshot()
        me = snap.procs[os.getpid()]
        assert me.rss > 0 and me.cmdline
        assert port in me.ports
        assert rc.why_listed(me) is not None
    finally:
        s.close()


def test_stop_process_tree():
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
    parent = subprocess.Popen([sys.executable, "-c", code])
    try:
        for _ in range(50):
            kids = psutil.Process(parent.pid).children()
            if kids:
                break
            time.sleep(0.1)
        assert kids
        stopped, errors = rcol.stop_process(parent.pid, tree=True, timeout=3)
        assert not errors
        assert parent.pid in stopped and kids[0].pid in stopped
        assert not psutil.pid_exists(kids[0].pid) or psutil.Process(kids[0].pid).status() == psutil.STATUS_ZOMBIE
    finally:
        parent.kill()


def test_stop_refuses_self():
    stopped, errors = rcol.stop_process(os.getpid(), tree=False)
    assert stopped == [] and "will not stop itself" in errors[0]


def test_stop_missing_pid():
    stopped, errors = rcol.stop_process(2 ** 22 + 12345, tree=False)
    assert stopped == [] and errors


def _spawn_family(n=3):
    code = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
            "time.sleep(120)")
    parents = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(n)]
    kids = []
    for par in parents:
        for _ in range(80):
            k = psutil.Process(par.pid).children()
            if k:
                kids.append(k[0].pid)
                break
            time.sleep(0.1)
    return parents, kids


def test_stop_many_tree_with_gone_and_refused():
    parents, kids = _spawn_family(3)
    try:
        assert len(kids) == 3
        gone_pid = subprocess.Popen([sys.executable, "-c", "pass"])
        gone_pid.wait()
        pids = [p.pid for p in parents] + [gone_pid.pid, os.getpid()]
        res = rcol.stop_many(pids, tree=True, timeout=3)
        for pid in [p.pid for p in parents] + kids:
            assert res[pid] == "stopped", res
        assert res[gone_pid.pid] == "gone" and res[os.getpid()] == "refused"
        text, counts = rc.stop_summary(res)
        assert counts["stopped"] == 6
    finally:
        for p in parents:
            p.kill()


def test_stop_many_without_tree_leaves_children():
    parents, kids = _spawn_family(1)
    try:
        res = rcol.stop_many([parents[0].pid], tree=False, timeout=3)
        assert res == {parents[0].pid: "stopped"}
        assert psutil.pid_exists(kids[0])
    finally:
        for k in kids:
            try:
                psutil.Process(k).kill()
            except psutil.Error:
                pass
