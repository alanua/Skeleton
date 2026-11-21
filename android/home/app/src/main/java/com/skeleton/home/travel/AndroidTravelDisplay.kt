package com.skeleton.home.travel

import androidx.activity.ComponentActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import android.app.Presentation
import android.app.UiModeManager
import android.content.Context
import android.content.res.Configuration
import android.hardware.display.DisplayManager
import android.media.MediaPlayer
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.Display
import android.view.SurfaceHolder
import android.view.SurfaceView

/** Explicit, foreground-only Travel owner. Create on the main thread after user opt-in,
 * call close from the host's onStop. Never attached to the Home/TV player automatically.
 * Display presence is a presentation capability, not proof of a physical HDMI connection.
 */
class AndroidTravelDisplay private constructor(
    private val activity: ComponentActivity,
    sessionId: String,
    remote: TravelRemoteSessionPort,
) : AutoCloseable, DisplayManager.DisplayListener, TravelLocalPlayer {
    private val displays = activity.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
    val session = TravelMediaSession(sessionId, this, remote)
    private var display: Display? = null
    private var presentation: Presentation? = null
    private var decoder: MediaPlayer? = null
    private var closed = false
    private var prepared = false
    private var wantsPlay = false
    private var pendingSeek = 0L
    private val lifecycleObserver = LifecycleEventObserver { _, event ->
        if (event == Lifecycle.Event.ON_STOP || event == Lifecycle.Event.ON_DESTROY) close()
    }

    companion object {
        /** Unsupported devices do not register listeners or create players. */
        fun open(activity: ComponentActivity, sessionId: String, remote: TravelRemoteSessionPort): AndroidTravelDisplay? {
            check(Looper.myLooper() == Looper.getMainLooper())
            check(activity.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED))
            val uiMode = activity.getSystemService(Context.UI_MODE_SERVICE) as UiModeManager
            if (!supportsTravelDisplay(Build.MANUFACTURER, Build.MODEL,
                    uiMode.currentModeType == Configuration.UI_MODE_TYPE_TELEVISION)) return null
            return AndroidTravelDisplay(activity, sessionId, remote).also {
                activity.lifecycle.addObserver(it.lifecycleObserver)
                it.displays.registerDisplayListener(it, Handler(Looper.getMainLooper()))
                it.refreshDisplay()
            }
        }
    }

    private fun checkThread() { check(Looper.myLooper() == Looper.getMainLooper()) }

    private fun refreshDisplay() {
        checkThread()
        if (closed) return
        val next = displays.getDisplays(DisplayManager.DISPLAY_CATEGORY_PRESENTATION)
            .filter { it.isValid && it.displayId != Display.DEFAULT_DISPLAY }
            .minByOrNull { it.displayId }
        if (display?.displayId != next?.displayId) {
            // Losing or replacing an output invalidates media and never migrates playback.
            session.updateHdmiAvailability(false)
            display = next
        }
        session.updateHdmiAvailability(next != null)
    }

    override fun onDisplayAdded(displayId: Int) = refreshDisplay()
    override fun onDisplayRemoved(displayId: Int) = refreshDisplay()
    override fun onDisplayChanged(displayId: Int) {
        // A mode/surface change also requires explicit reload.
        if (display?.displayId == displayId) session.updateHdmiAvailability(false)
        refreshDisplay()
    }

    override fun load(media: TravelMedia, output: TravelOutput) {
        checkThread()
        check(!closed && output == TravelOutput.HDMI) { "external_output_required" }
        stop()
        val target = requireNotNull(display).also { check(it.isValid) }
        val view = SurfaceView(activity)
        val window = Presentation(activity, target)
        presentation = window
        window.setContentView(view)
        window.setOnDismissListener {
            if (presentation === window && !closed) session.updateHdmiAvailability(false)
        }
        view.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                if (closed || presentation !== window) return
                try {
                    val player = MediaPlayer()
                    decoder = player
                    player.setDisplay(holder)
                    player.setOnPreparedListener {
                        if (decoder === it) {
                            try {
                                prepared = true
                                if (pendingSeek > 0) it.seekTo(pendingSeek, MediaPlayer.SEEK_CLOSEST)
                                if (wantsPlay) it.start()
                            } catch (_: Exception) { session.rendererFailed() }
                        }
                    }
                    player.setOnCompletionListener {
                        if (decoder === it) session.execute(TravelMediaCommand(
                            session.snapshot().sessionId, session.snapshot().revision, TravelAction.STOP))
                    }
                    player.setOnErrorListener { failed, _, _ ->
                        if (decoder === failed) session.rendererFailed()
                        true
                    }
                    player.setDataSource(activity, android.net.Uri.parse(media.uri))
                    player.prepareAsync()
                } catch (_: Exception) { session.rendererFailed() }
            }
            override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {}
            override fun surfaceDestroyed(holder: SurfaceHolder) {
                if (presentation === window && !closed) session.updateHdmiAvailability(false)
            }
        })
        window.show()
    }

    override fun play() { checkThread(); wantsPlay = true; if (prepared) decoder?.start() }
    override fun pause() { checkThread(); wantsPlay = false; if (prepared) decoder?.pause() }
    override fun seekTo(positionMs: Long) {
        checkThread()
        pendingSeek = positionMs
        if (prepared) decoder?.seekTo(positionMs, MediaPlayer.SEEK_CLOSEST)
    }
    override fun stop() {
        checkThread()
        val oldPlayer = decoder
        val oldWindow = presentation
        decoder = null
        presentation = null
        prepared = false
        wantsPlay = false
        pendingSeek = 0
        try { oldPlayer?.release() } finally { oldWindow?.dismiss() }
    }
    override fun close() {
        checkThread()
        if (closed) return
        closed = true
        activity.lifecycle.removeObserver(lifecycleObserver)
        displays.unregisterDisplayListener(this)
        session.close()
        display = null
    }
}
