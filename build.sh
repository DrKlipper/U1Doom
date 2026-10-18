#!/bin/sh
# Loads everything U1 DOOM needs and builds build/doom for the Snapmaker U1.
#
#   ./build.sh
#
# Downloads into download/ (checked with SHA-256) and unpacks:
#   toolchain/    Bootlin aarch64 glibc stable-2023.11-1 (gcc 12.3, glibc 2.38 like the U1)
#   sdl/          SDL2 2.32.10 and SDL2_mixer 2.8.2: sound and controllers
#   doomgeneric/  the DOOM engine (github.com/ozkl/doomgeneric, fixed commit)
#   wad/          Freedoom 0.13.0
# Then builds SDL2 and SDL2_mixer (sdl/aarch64) and DOOM (build/doom).
# Steps already done are skipped, so the script can simply run again.
set -e
cd "$(dirname "$0")"
ROOT=$(pwd)

TOOLCHAIN=aarch64--glibc--stable-2023.11-1
SDL=SDL2-2.32.10
MIXER=SDL2_mixer-2.8.2
DOOMGENERIC_COMMIT=e257a09527062bfb986b6e5e0780839b6cbd5b71

# The toolchain brings the compiler for the U1; configure and make run here.
# SDL only takes the ALSA and libudev headers from this machine, the libraries
# come from the U1 at run time (so bluealsa's ALSA plugin works).
missing=""
for tool in curl tar bzip2 unzip git make sha256sum; do
    command -v $tool >/dev/null || missing="$missing $tool"
done
[ -d /usr/include/alsa ] || missing="$missing ALSA-Header"
[ -f /usr/include/libudev.h ] || missing="$missing libudev-Header"
if [ -n "$missing" ]; then
    echo "Es fehlt:$missing"
    echo "Debian/Ubuntu: sudo apt install make curl bzip2 unzip git libasound2-dev libudev-dev"
    exit 1
fi

# fetch <url> <sha256>: loads into download/ once, checks it every time.
fetch() {
    file=download/$(basename "$1")
    if [ ! -f "$file" ]; then
        echo "Lade $(basename "$1") ..."
        mkdir -p download
        curl -fL --progress-bar -o "$file.part" "$1"
        mv "$file.part" "$file"
    fi
    echo "$2  $file" | sha256sum -c --quiet
}

if [ ! -x toolchain/bin/aarch64-linux-gcc ]; then
    fetch https://toolchains.bootlin.com/downloads/releases/toolchains/aarch64/tarballs/$TOOLCHAIN.tar.bz2 \
        5b1fde169c9ed678ee75a729286531a291c6ce5ee9d8a8b598b3abbef35c05f2
    echo "Entpacke die Toolchain ..."
    rm -rf toolchain $TOOLCHAIN
    tar xjf download/$TOOLCHAIN.tar.bz2
    mv $TOOLCHAIN toolchain
fi

if [ ! -d sdl/$SDL ]; then
    fetch https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/$SDL.tar.gz \
        5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165
    mkdir -p sdl && tar xzf download/$SDL.tar.gz -C sdl
fi
if [ ! -d sdl/$MIXER ]; then
    fetch https://github.com/libsdl-org/SDL_mixer/releases/download/release-2.8.2/$MIXER.tar.gz \
        938dff531d00ace2296557a6599abe6f34599e2f34f0a4a08a397e2ccac8b8f7
    mkdir -p sdl && tar xzf download/$MIXER.tar.gz -C sdl
fi

if [ ! -d doomgeneric ]; then
    echo "Lade doomgeneric ..."
    rm -rf doomgeneric.part
    git clone --quiet https://github.com/ozkl/doomgeneric.git doomgeneric.part
    git -C doomgeneric.part checkout --quiet $DOOMGENERIC_COMMIT
    mv doomgeneric.part doomgeneric
fi

if [ ! -f wad/freedoom2.wad ]; then
    fetch https://github.com/freedoom/freedoom/releases/download/v0.13.0/freedoom-0.13.0.zip \
        3f9b264f3e3ce503b4fb7f6bdcb1f419d93c7b546f4df3e874dd878db9688f59
    mkdir -p wad
    unzip -o -j -q download/freedoom-0.13.0.zip -d wad \
        freedoom-0.13.0/COPYING.txt freedoom-0.13.0/freedoom1.wad freedoom-0.13.0/freedoom2.wad
fi

# Static SDL2 and SDL2_mixer for the U1. SDL only takes sound and controllers:
# the picture goes straight to /dev/fb0 (SDL2's KMSDRM driver needs GBM/EGL,
# and the U1 has no GPU driver). No music: DOOM runs with -nomusic.
#
# Autotools cannot cope with spaces in the path, so the build runs in a
# temporary directory that reaches this folder through a symlink.
if [ ! -f sdl/aarch64/lib/libSDL2_mixer.a ]; then (
    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    ln -s "$ROOT" "$WORK/p"
    P="$WORK/p"
    PREFIX="$P/sdl/aarch64"
    EXTRA="$WORK/extra"

    mkdir -p "$EXTRA/include" "$EXTRA/lib" "$WORK/nopc"
    cp -r /usr/include/alsa "$EXTRA/include/"
    cp /usr/include/libudev.h "$EXTRA/include/"
    # SDL's configure takes the soname to dlopen from the library file name.
    touch "$EXTRA/lib/libasound.so.2"
    ln -s libasound.so.2 "$EXTRA/lib/libasound.so"

    export CC="$P/toolchain/bin/aarch64-linux-gcc"
    export AR="$P/toolchain/bin/aarch64-linux-ar"
    export RANLIB="$P/toolchain/bin/aarch64-linux-ranlib"
    export CPPFLAGS="-I$EXTRA/include"
    export PKG_CONFIG_LIBDIR="$WORK/nopc"   # never pick up this machine's .pc files
    HOST=aarch64-buildroot-linux-gnu

    echo "Baue SDL2 ..."
    mkdir "$WORK/sdl2" && cd "$WORK/sdl2"
    "$P/sdl/$SDL/configure" --host=$HOST --prefix="$PREFIX" \
        --enable-static --disable-shared \
        --disable-video --disable-render \
        --enable-alsa --enable-alsa-shared --disable-alsatest \
        --with-alsa-prefix="$EXTRA/lib" --with-alsa-inc-prefix="$EXTRA/include" \
        --disable-oss --disable-jack --disable-esd --disable-pipewire \
        --disable-pulseaudio --disable-arts --disable-nas --disable-sndio \
        --disable-libsamplerate --disable-dbus --disable-ime --disable-ibus \
        --disable-fcitx --enable-libudev --enable-hidapi --disable-hidapi-libusb >/dev/null
    make -j"$(nproc)" >/dev/null
    make install >/dev/null
    # Without the .la file libtool treats -lSDL2 as a plain flag instead of
    # resolving it to the real path (with the space) and archiving it.
    rm -f "$PREFIX"/lib/*.la
    # sdl2-config resolves its prefix with `pwd -P`, i.e. to the real path;
    # SDL_mixer gets the flags from SDL_CFLAGS/SDL_LIBS instead.
    rm -f "$PREFIX/bin/sdl2-config"

    echo "Baue SDL2_mixer ..."
    export SDL_CFLAGS="-I$PREFIX/include/SDL2 -D_REENTRANT"
    export SDL_LIBS="-L$PREFIX/lib -lSDL2"
    mkdir "$WORK/mixer" && cd "$WORK/mixer"
    "$P/sdl/$MIXER/configure" --host=$HOST --prefix="$PREFIX" \
        --enable-static --disable-shared --disable-sdltest \
        --disable-music-cmd \
        --disable-music-mod --disable-music-midi --disable-music-ogg \
        --disable-music-flac --disable-music-mp3 --disable-music-opus \
        --disable-music-wavpack --disable-music-gme >/dev/null
    make -j"$(nproc)" >/dev/null
    make install >/dev/null
)
fi

echo "Baue DOOM ..."
make -j"$(nproc)" --no-print-directory
echo "Fertig: build/doom. Starten mit ./start.sh (Windows: start.cmd)."
