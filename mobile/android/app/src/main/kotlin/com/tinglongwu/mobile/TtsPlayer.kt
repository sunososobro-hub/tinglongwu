package com.tinglongwu.mobile

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Build

/// Conversation-mode read-aloud (#558): plays one synthesized line (mono float PCM) on the
/// phone speaker with a static AudioTrack. Dart queues lines one at a time (#1953) and waits
/// for [whenDone]; while a line plays the app holds transient audio focus that lets other apps
/// duck, so a video playing at the same time gets quieter.
object TtsPlayer {
    private var track: AudioTrack? = null
    private var audio: AudioManager? = null
    private var focus: AudioFocusRequest? = null
    private val waiters = mutableListOf<() -> Unit>()

    private val attrs: AudioAttributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()

    fun init(context: Context) {
        audio = context.applicationContext.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
    }

    /// Returns true once playback has started.
    @Synchronized
    fun play(samples: FloatArray, sampleRate: Int): Boolean {
        stop()
        if (samples.isEmpty() || sampleRate <= 0) return false
        return try {
            val t = AudioTrack.Builder()
                .setAudioAttributes(attrs)
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
            duck()
            t.play()
            track = t
            true
        } catch (e: Exception) {
            calm()
            false
        }
    }

    /// Calls [cb] once the current line has ended (at once when nothing plays).
    @Synchronized
    fun whenDone(cb: () -> Unit) {
        if (track == null) cb() else waiters.add(cb)
    }

    /// A headset (wired, USB or Bluetooth) is among the outputs.
    @SuppressLint("InlinedApi")
    fun headset(): Boolean {
        val am = audio ?: return false
        val kinds = setOf(
            AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
            AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_BLE_HEADSET)
        return am.getDevices(AudioManager.GET_DEVICES_OUTPUTS).any { it.type in kinds }
    }

    @Synchronized
    fun stop() {
        val t = track ?: return
        try { t.stop() } catch (_: Exception) {}
        release(t)
    }

    @Synchronized
    private fun release(t: AudioTrack) {
        if (track === t) {
            track = null
            calm()
            val done = waiters.toList()
            waiters.clear()
            done.forEach { it() }
        }
        try { t.release() } catch (_: Exception) {}
    }

    /// Transient focus, other apps may duck (lower their volume) instead of pausing.
    @Suppress("DEPRECATION")
    private fun duck() {
        val am = audio ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val r = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
                .setAudioAttributes(attrs)
                .build()
            focus = r
            am.requestAudioFocus(r)
        } else {
            am.requestAudioFocus(null, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        }
    }

    @Suppress("DEPRECATION")
    private fun calm() {
        val am = audio ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            focus?.let { am.abandonAudioFocusRequest(it) }
            focus = null
        } else {
            am.abandonAudioFocus(null)
        }
    }
}
