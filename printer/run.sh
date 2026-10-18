#!/bin/sh
# Starts DOOM on the U1 display. Stops the Snapmaker display app first and
# brings it back when DOOM ends, however it ends. Starts bluealsa so a
# connected Bluetooth speaker plays the sound.
#
# Usage: run.sh [freedoom1.wad | freedoom2.wad | other .wad in this folder]

cd "$(dirname "$0")" || exit 1
lmd_cli -K gui >/dev/null
trap 'lmd_cli -S gui >/dev/null' EXIT
trap 'exit 1' INT TERM HUP

if ! pidof bluealsa >/dev/null; then
    bluealsa -S -p a2dp-source &
    sleep 1
fi

# "bluealsa" is the speaker that connected last (defaults in
# /etc/alsa/conf.d/20-bluealsa.conf). SDL_mixer has no MIDI here: -nomusic.
AUDIODEV=bluealsa ./doom -iwad "${1:-freedoom1.wad}" -nomusic
