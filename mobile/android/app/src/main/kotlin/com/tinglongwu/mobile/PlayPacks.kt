package com.tinglongwu.mobile

import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import com.google.android.play.core.assetpacks.AssetPackManager
import com.google.android.play.core.assetpacks.AssetPackManagerFactory
import com.google.android.play.core.assetpacks.AssetPackState
import com.google.android.play.core.assetpacks.AssetPackStateUpdateListener
import com.google.android.play.core.assetpacks.model.AssetPackStatus
import io.flutter.plugin.common.MethodChannel

/**
 * Model download through Google Play Asset Delivery (on-demand packs `models_asr`, `models_llm`).
 * The Play Store downloads the packs, so the app still has no INTERNET permission. Works only when
 * the app was installed from Play; a side-loaded APK gets `available = false` and keeps the manual
 * zip import. Dart copies the files out of the pack folders through the normal checked import
 * (size + SHA-256 against the manifest) and then calls `remove` to free the space.
 */
class PlayPacks(
    activity: android.app.Activity,
    private val confirm: ActivityResultLauncher<IntentSenderRequest>,
) {
    private val apm: AssetPackManager = AssetPackManagerFactory.getInstance(activity.applicationContext)
    private var listener: AssetPackStateUpdateListener? = null
    private var pending: MethodChannel.Result? = null
    private var asked = false

    /** {available, totalBytes, paths: {pack: assetsPath}} ; paths only for packs already on the device. */
    fun status(result: MethodChannel.Result) {
        apm.getPackStates(PACKS)
            .addOnSuccessListener { s ->
                result.success(
                    mapOf("available" to true, "totalBytes" to s.totalBytes(), "paths" to paths())
                )
            }
            .addOnFailureListener { e ->
                // Not installed from Play (or Play Store missing): no packs to fetch.
                result.success(mapOf("available" to false, "error" to (e.message ?: e.toString())))
            }
    }

    /**
     * Downloads every pack not yet on the device. Progress goes to [onProgress] (bytes, total, status).
     * Completes with {pack: assetsPath} once all packs are COMPLETED.
     */
    fun fetch(result: MethodChannel.Result, onProgress: (Long, Long, Int) -> Unit) {
        if (pending != null) { result.error("BUSY", "download already running", null); return }
        if (PACKS.all { apm.getPackLocation(it) != null }) { result.success(paths()); return }
        pending = result
        asked = false
        val states = HashMap<String, AssetPackState>()
        val l = AssetPackStateUpdateListener { st ->
            if (st.name() !in PACKS) return@AssetPackStateUpdateListener
            states[st.name()] = st
            val done = states.values.sumOf { it.bytesDownloaded() }
            val total = states.values.sumOf { it.totalBytesToDownload() }
            onProgress(done, total, st.status())
            when (st.status()) {
                AssetPackStatus.REQUIRES_USER_CONFIRMATION, AssetPackStatus.WAITING_FOR_WIFI -> {
                    // Large download on mobile data, or Play wants consent: show Play's own dialog once.
                    if (!asked) { asked = true; apm.showConfirmationDialog(confirm) }
                }
                AssetPackStatus.FAILED -> finishError("FAILED", "${st.name()}: Play error ${st.errorCode()}")
                AssetPackStatus.CANCELED -> finishError("CANCELLED", "cancelled")
                AssetPackStatus.COMPLETED -> {
                    if (PACKS.all { apm.getPackLocation(it) != null }) finishOk()
                }
                else -> {}
            }
        }
        listener = l
        apm.registerListener(l)
        apm.fetch(PACKS).addOnFailureListener { e -> finishError("FAILED", e.message ?: e.toString()) }
    }

    /** Result of Play's confirmation dialog (user declined = stop the download). */
    fun onConfirmResult(ok: Boolean) {
        if (!ok) { apm.cancel(PACKS); finishError("CANCELLED", "download not confirmed") }
    }

    fun cancel() { apm.cancel(PACKS) }

    /** Frees the pack storage after the files were copied into the models dir. */
    fun remove(result: MethodChannel.Result) {
        var left = PACKS.size
        for (p in PACKS) {
            apm.removePack(p).addOnCompleteListener { if (--left == 0) result.success(null) }
        }
    }

    private fun paths(): Map<String, String> =
        PACKS.mapNotNull { p -> apm.getPackLocation(p)?.assetsPath()?.let { p to it } }.toMap()

    private fun finishOk() {
        val r = pending ?: return
        cleanup(); r.success(paths())
    }

    private fun finishError(code: String, msg: String) {
        val r = pending ?: return
        cleanup(); r.error(code, msg, null)
    }

    private fun cleanup() {
        pending = null
        listener?.let { apm.unregisterListener(it) }
        listener = null
    }

    companion object {
        val PACKS = listOf("models_asr", "models_llm")
    }
}
