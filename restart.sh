git pull
# restart.sh is normally launched from the running comma tmux session, which can
# still carry the previous checkout's AGNOS_VERSION (18.4 before the 19.8 bump).
# Let the newly pulled launch_env.sh select its own version.
unset AGNOS_VERSION
tmux set-environment -gu AGNOS_VERSION 2>/dev/null || true
tmux kill-session -t comma; rm -f /tmp/safe_staging_overlay.lock; sleep 1;tmux new -s comma -d "bash -lc '/data/openpilot/launch_openpilot.sh'"
