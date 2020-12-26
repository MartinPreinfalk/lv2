/*
  LV2 Metronome Example Plugin
  Copyright 2012-2016 David Robillard <d@drobilla.net>

  Permission to use, copy, modify, and/or distribute this software for any
  purpose with or without fee is hereby granted, provided that the above
  copyright notice and this permission notice appear in all copies.

  THIS SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
  WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
  MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
  ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
  WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
  ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
  OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
*/

#include "lv2/atom/atom.h"
#include "lv2/atom/util.h"
#include "lv2/core/lv2.h"
#include "lv2/core/lv2_util.h"
#include "lv2/log/log.h"
#include "lv2/log/logger.h"
#include "lv2/time/time.h"
#include "lv2/urid/urid.h"

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#    define M_PI 3.14159265
#endif

#define EG_METRO_URI "http://lv2plug.in/plugins/eg-metro"

typedef struct {
	LV2_URID atom_Blank;
	LV2_URID atom_Float;
	LV2_URID atom_Object;
	LV2_URID atom_Path;
	LV2_URID atom_Resource;
	LV2_URID atom_Sequence;
	LV2_URID time_Position;
	LV2_URID time_barBeat;
	LV2_URID time_beatsPerMinute;
	LV2_URID time_speed;
} MetroURIs;

static const double attack_s = 0.005;
static const double decay_s  = 0.075;

enum {
	METRO_CONTROL = 0,
	METRO_OUT     = 1
};

/** During execution this plugin can be in one of 3 states: */
typedef enum {
	STATE_ATTACK,  // Envelope rising
	STATE_DECAY,   // Envelope lowering
	STATE_OFF      // Silent
} State;

/**
   This plugin must keep track of more state than previous examples to be able
   to render audio.  The basic idea is to generate a single cycle of a sine
   wave which is conceptually played continuously.  The 'tick' is generated by
   enveloping the amplitude so there is a short attack/decay peak around a
   tick, and silence the rest of the time.

   This example uses a simple AD envelope with fixed parameters.  A more
   sophisticated implementation might use a more advanced envelope and allow
   the user to modify these parameters, the frequency of the wave, and so on.
*/
typedef struct {
	LV2_URID_Map*  map;     // URID map feature
	LV2_Log_Logger logger;  // Logger API
	MetroURIs      uris;    // Cache of mapped URIDs

	struct {
		LV2_Atom_Sequence* control;
		float*             output;
	} ports;

	// Variables to keep track of the tempo information sent by the host
	double rate;   // Sample rate
	float  bpm;    // Beats per minute (tempo)
	float  speed;  // Transport speed (usually 0=stop, 1=play)

	uint32_t elapsed_len;  // Frames since the start of the last click
	uint32_t wave_offset;  // Current play offset in the wave
	State    state;        // Current play state

	// One cycle of a sine wave
	float*   wave;
	uint32_t wave_len;

	// Envelope parameters
	uint32_t attack_len;
	uint32_t decay_len;
} Metro;

static void
connect_port(LV2_Handle instance,
             uint32_t   port,
             void*      data)
{
	Metro* self = (Metro*)instance;

	switch (port) {
	case METRO_CONTROL:
		self->ports.control = (LV2_Atom_Sequence*)data;
		break;
	case METRO_OUT:
		self->ports.output = (float*)data;
		break;
	default:
		break;
	}
}

/**
   The activate() method resets the state completely, so the wave offset is
   zero and the envelope is off.
*/
static void
activate(LV2_Handle instance)
{
	Metro* self = (Metro*)instance;

	self->elapsed_len = 0;
	self->wave_offset = 0;
	self->state       = STATE_OFF;
}

/**
   This plugin does a bit more work in instantiate() than the previous
   examples.  The tempo updates from the host contain several URIs, so those
   are mapped, and the sine wave to be played needs to be generated based on
   the current sample rate.
*/
static LV2_Handle
instantiate(const LV2_Descriptor*     descriptor,
            double                    rate,
            const char*               path,
            const LV2_Feature* const* features)
{
	Metro* self = (Metro*)calloc(1, sizeof(Metro));
	if (!self) {
		return NULL;
	}

	// Scan host features for URID map
	// clang-format off
	const char* missing = lv2_features_query(
		features,
		LV2_LOG__log,  &self->logger.log, false,
		LV2_URID__map, &self->map, true,
		NULL);
	// clang-format on

	lv2_log_logger_set_map(&self->logger, self->map);
	if (missing) {
		lv2_log_error(&self->logger, "Missing feature <%s>\n", missing);
		free(self);
		return NULL;
	}

	// Map URIS
	MetroURIs* const    uris  = &self->uris;
	LV2_URID_Map* const map   = self->map;
	uris->atom_Blank          = map->map(map->handle, LV2_ATOM__Blank);
	uris->atom_Float          = map->map(map->handle, LV2_ATOM__Float);
	uris->atom_Object         = map->map(map->handle, LV2_ATOM__Object);
	uris->atom_Path           = map->map(map->handle, LV2_ATOM__Path);
	uris->atom_Resource       = map->map(map->handle, LV2_ATOM__Resource);
	uris->atom_Sequence       = map->map(map->handle, LV2_ATOM__Sequence);
	uris->time_Position       = map->map(map->handle, LV2_TIME__Position);
	uris->time_barBeat        = map->map(map->handle, LV2_TIME__barBeat);
	uris->time_beatsPerMinute = map->map(map->handle, LV2_TIME__beatsPerMinute);
	uris->time_speed          = map->map(map->handle, LV2_TIME__speed);

	// Initialise instance fields
	self->rate       = rate;
	self->bpm        = 120.0f;
	self->attack_len = (uint32_t)(attack_s * rate);
	self->decay_len  = (uint32_t)(decay_s * rate);
	self->state      = STATE_OFF;

	// Generate one cycle of a sine wave at the desired frequency
	const double freq = 440.0 * 2.0;
	const double amp  = 0.5;
	self->wave_len = (uint32_t)(rate / freq);
	self->wave     = (float*)malloc(self->wave_len * sizeof(float));
	for (uint32_t i = 0; i < self->wave_len; ++i) {
		self->wave[i] = (float)(sin(i * 2 * M_PI * freq / rate) * amp);
	}

	return (LV2_Handle)self;
}

static void
cleanup(LV2_Handle instance)
{
	free(instance);
}

/**
   Play back audio for the range [begin..end) relative to this cycle.  This is
   called by run() in-between events to output audio up until the current time.
*/
static void
play(Metro* self, uint32_t begin, uint32_t end)
{
	float* const   output          = self->ports.output;
	const uint32_t frames_per_beat = 60.0f / self->bpm * self->rate;

	if (self->speed == 0.0f) {
		memset(output, 0, (end - begin) * sizeof(float));
		return;
	}

	for (uint32_t i = begin; i < end; ++i) {
		switch (self->state) {
		case STATE_ATTACK:
			// Amplitude increases from 0..1 until attack_len
			output[i] = self->wave[self->wave_offset] *
				self->elapsed_len / (float)self->attack_len;
			if (self->elapsed_len >= self->attack_len) {
				self->state = STATE_DECAY;
			}
			break;
		case STATE_DECAY:
			// Amplitude decreases from 1..0 until attack_len + decay_len
			output[i] = 0.0f;
			output[i] = self->wave[self->wave_offset] *
				(1 - ((float)(self->elapsed_len - self->attack_len) /
				      (float)self->decay_len));
			if (self->elapsed_len >= self->attack_len + self->decay_len) {
				self->state = STATE_OFF;
			}
			break;
		case STATE_OFF:
			output[i] = 0.0f;
		}

		// We continuously play the sine wave regardless of envelope
		self->wave_offset = (self->wave_offset + 1) % self->wave_len;

		// Update elapsed time and start attack if necessary
		if (++self->elapsed_len == frames_per_beat) {
			self->state       = STATE_ATTACK;
			self->elapsed_len = 0;
		}
	}
}

/**
   Update the current position based on a host message.  This is called by
   run() when a time:Position is received.
*/
static void
update_position(Metro* self, const LV2_Atom_Object* obj)
{
	const MetroURIs* uris = &self->uris;

	// Received new transport position/speed
	LV2_Atom* beat  = NULL;
	LV2_Atom* bpm   = NULL;
	LV2_Atom* speed = NULL;

	// clang-format off
	lv2_atom_object_get(obj,
	                    uris->time_barBeat, &beat,
	                    uris->time_beatsPerMinute, &bpm,
	                    uris->time_speed, &speed,
	                    NULL);
	// clang-format on

	if (bpm && bpm->type == uris->atom_Float) {
		// Tempo changed, update BPM
		self->bpm = ((LV2_Atom_Float*)bpm)->body;
	}
	if (speed && speed->type == uris->atom_Float) {
		// Speed changed, e.g. 0 (stop) to 1 (play)
		self->speed = ((LV2_Atom_Float*)speed)->body;
	}
	if (beat && beat->type == uris->atom_Float) {
		// Received a beat position, synchronise
		// This hard sync may cause clicks, a real plugin would be more graceful
		const float frames_per_beat = (float)(60.0 / self->bpm * self->rate);
		const float bar_beats       = ((LV2_Atom_Float*)beat)->body;
		const float beat_beats      = bar_beats - floorf(bar_beats);
		self->elapsed_len           = beat_beats * frames_per_beat;
		if (self->elapsed_len < self->attack_len) {
			self->state = STATE_ATTACK;
		} else if (self->elapsed_len < self->attack_len + self->decay_len) {
			self->state = STATE_DECAY;
		} else {
			self->state = STATE_OFF;
		}
	}
}

static void
run(LV2_Handle instance, uint32_t sample_count)
{
	Metro*           self = (Metro*)instance;
	const MetroURIs* uris = &self->uris;

	// Work forwards in time frame by frame, handling events as we go
	const LV2_Atom_Sequence* in     = self->ports.control;
	uint32_t                 last_t = 0;
	for (const LV2_Atom_Event* ev = lv2_atom_sequence_begin(&in->body);
	     !lv2_atom_sequence_is_end(&in->body, in->atom.size, ev);
	     ev = lv2_atom_sequence_next(ev)) {

		// Play the click for the time slice from last_t until now
		play(self, last_t, ev->time.frames);

		// Check if this event is an Object
		// (or deprecated Blank to tolerate old hosts)
		if (ev->body.type == uris->atom_Object ||
		    ev->body.type == uris->atom_Blank) {
			const LV2_Atom_Object* obj = (const LV2_Atom_Object*)&ev->body;
			if (obj->body.otype == uris->time_Position) {
				// Received position information, update
				update_position(self, obj);
			}
		}

		// Update time for next iteration and move to next event
		last_t = ev->time.frames;
	}

	// Play for remainder of cycle
	play(self, last_t, sample_count);
}

static const LV2_Descriptor descriptor = {
	EG_METRO_URI,
	instantiate,
	connect_port,
	activate,
	run,
	NULL,  // deactivate,
	cleanup,
	NULL,  // extension_data
};

LV2_SYMBOL_EXPORT const LV2_Descriptor*
lv2_descriptor(uint32_t index)
{
	return index == 0 ? &descriptor : NULL;
}
