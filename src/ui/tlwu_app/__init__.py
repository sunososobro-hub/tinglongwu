"""tlwu.exe launcher for the standalone Windows build (double-click -> GUI, first-run setup screen).

Deliberately a separate package: it may start the networked setup (always in a child process) and the offline
runtime `tinglongwu`, but it never imports `tinglongwu_setup` and `tinglongwu` into the same process.
"""
