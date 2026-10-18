# Cross build of doomgeneric for the Snapmaker U1 (aarch64, glibc 2.38).
# The Bootlin toolchain in toolchain/ matches the U1's Buildroot 2024.02 glibc.

# SDL2 and SDL2_mixer (sound, controllers) come from build-sdl.sh.

CC     = toolchain/bin/aarch64-linux-gcc
DG     = doomgeneric/doomgeneric
SDL    = sdl/aarch64
CFLAGS = -O2 -Wall -Wno-unused -DNORMALUNIX -DLINUX -D_DEFAULT_SOURCE \
         -DDOOMGENERIC_RESX=320 -DDOOMGENERIC_RESY=200 -DFEATURE_SOUND \
         -I$(DG) -I$(SDL)/include/SDL2 -D_REENTRANT
LIBS   = $(SDL)/lib/libSDL2_mixer.a $(SDL)/lib/libSDL2.a -lm -ldl -lpthread -lrt

# The engine files, as listed in doomgeneric's Makefile.linuxvt.
DOOM = dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop \
       d_main d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus \
       i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox \
       m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng \
       p_doors p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj p_plats \
       p_pspr p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user \
       r_bsp r_data r_draw r_main r_plane r_segs r_sky r_things sha1 sounds \
       statdump st_lib st_stuff s_sound tables v_video wi_stuff w_checksum \
       w_file w_main w_wad z_zone w_file_stdc i_input i_video doomgeneric mus2mid \
       i_sdlsound i_sdlmusic
OBJS = $(addprefix build/obj/,$(addsuffix .o,$(DOOM))) build/obj/doomgeneric_u1.o

build/doom: $(OBJS)
	$(CC) -o $@ $(OBJS) $(LIBS)

build/obj/%.o: $(DG)/%.c | build/obj
	$(CC) $(CFLAGS) -c $< -o $@

build/obj/doomgeneric_u1.o: src/doomgeneric_u1.c | build/obj
	$(CC) $(CFLAGS) -c $< -o $@

build/obj:
	mkdir -p $@

clean:
	rm -rf build

.PHONY: clean
