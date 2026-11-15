package com.tinglongwu.mobile

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack

/// Conversation-mode read-aloud (#558): plays one synthesized line (mono float PCM) on the
/// phone speaker with a static AudioTrack. A new line replaces the one still playing.
object TtsPlayer {
    private var track: AudioTrack? = null

    /// Returns true once playback has started.
    @Synchronized
    fun play(samples: FloatArray, sampleRate: Int): Boolean {
        stop()
        if (samples.isEmpty() || sampleRate <= 0) return false
        return try {
            val t = AudioTrack.Builder()
                .setAudioAttributes(AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build())
                .setAudioFormat(AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
                    .setSampleRate(sampleRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build())
                .setTransferMode(AudioTrack.MODE_STATIC)
                .setBufferSizeInBytes(samples.size * 4)
                .build()
            t.write(samples, 0, samples.size, AudioTrack.WRITE_BLOCKING)
            t.setNotificationMarkerPosition(samples.size)
            t.setPlaybackPositionUpdateListener(object : AudioTrack.OnPlaybackPositionUpdateListener {
                override fun onMarkerReached(at: AudioTrack) = release(at)
                override fun onPeriodicNotification(at: AudioTrack) {}
            })
            t.play()
            track = t
            true
        } catch (e: Exception) {
            false
        }
    }

    @Synchronized
    fun stop() {
        val t = track ?: return
        try { t.stop() } catch (_: Exception) {}
        release(t)
    }

    @Synchronized
    private fun release(t: AudioTrack) {
        if (track === t) track = null
        try { t.release() } catch (_: Exception) {}
    }
}
