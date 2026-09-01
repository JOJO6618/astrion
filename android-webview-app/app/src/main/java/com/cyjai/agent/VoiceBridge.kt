package com.cyjai.agent

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.core.app.ActivityCompat
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineSenseVoiceModelConfig
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.Vad
import com.k2fsa.sherpa.onnx.VadModelConfig
import com.k2fsa.sherpa.onnx.SileroVadModelConfig
import com.k2fsa.sherpa.onnx.SpeechSegment
import android.os.Environment
import kotlinx.coroutines.*
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 语音识别桥接 — 通过 JS Bridge 暴露给 WebView
 *
 * 前端调用：
 *   window.AndroidVoiceBridge.startListening()    // 开始录音
 *   window.AndroidVoiceBridge.stopListening()     // 停止录音
 *   window.AndroidVoiceBridge.isSupported()       // 是否支持 → true
 *
 * 结果通过全局回调传回前端：
 *   window.__onVoiceResult(text)                  // 识别结果
 *   window.__onVoiceStatus(status)                // 状态变化: "idle"|"listening"|"processing"
 *   window.__onVoiceError(error)                  // 错误
 */
class VoiceBridge(
    private val context: Context,
    private val webView: WebView
) {
    companion object {
        private const val TAG = "VoiceBridge"
        private const val SAMPLE_RATE = 16000
        private const val CHUNK_SIZE = 512 // 每次读取的采样数
    }

    // ── 状态 ──
    private val isRecording = AtomicBoolean(false)
    private var audioRecord: AudioRecord? = null
    private var recordingJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var recordingStartTime = 0L        // 录音开始时间戳（用于防抖）
    private val MIN_RECORDING_MS = 400L          // 最小录音时长（防抖）

    // ── sherpa-onnx 引擎 ──
    private var recognizer: OfflineRecognizer? = null
    private var vad: Vad? = null
    private var initialized = false
    private var engineInitJob: Job? = null  // 预初始化任务

    // ── 调试日志 ──
    private val logFile: File
        get() = File(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "voice_debug.log")
    private val dateFmt = SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.getDefault())

    private fun logToFile(msg: String) {
        try {
            val ts = dateFmt.format(Date())
            logFile.parentFile?.mkdirs()
            FileWriter(logFile, true).use { it.write("[$ts] $msg\n") }
        } catch (_: Exception) {}
    }

    // ── 模型路径 ──
    private val modelDir: File
        get() = File(context.filesDir, "voice_models")
    private val senseVoiceModel: File
        get() = File(modelDir, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/model.int8.onnx")
    private val senseVoiceTokens: File
        get() = File(modelDir, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/tokens.txt")
    private val sileroVadModel: File
        get() = File(modelDir, "silero_vad.onnx")

    // ═══════════════════ JS Bridge 接口 ═══════════════════

    @JavascriptInterface
    fun isSupported(): Boolean = true

    /** 模型是否已就绪（含文件大小校验） */
    @JavascriptInterface
    fun isModelReady(): Boolean {
        return ModelManager.isModelReady(context)
    }

    /** 是否有模型文件残留（存在但不完整，需清理） */
    @JavascriptInterface
    fun isModelPartial(): Boolean {
        val filesExist = senseVoiceModel.exists() || senseVoiceTokens.exists() || sileroVadModel.exists()
        return filesExist && !ModelManager.isModelReady(context)
    }

    /** 删除已下载的模型文件 */
    @JavascriptInterface
    fun deleteModel(): Boolean {
        releaseEngine()
        return ModelManager.deleteModels(context)
    }

    /** 触发模型下载（供个人空间手动下载），下载前先释放引擎并清理旧文件 */
    @JavascriptInterface
    fun downloadModel() {
        scope.launch {
            try {
                // 先释放引擎（避免文件被 onnxruntime 锁定导致删除失败）
                releaseEngine()
                ModelManager.deleteModels(context)
                postToJs("window.__onVoiceDownloadProgress(0, '开始下载...')")
                val success = ModelManager.downloadModels(context) { pct, msg ->
                    postToJs("window.__onVoiceDownloadProgress($pct, '${escapeJs(msg)}')")
                }
                if (success) {
                    postToJs("window.__onVoiceDownloadProgress(100, '下载完成')")
                    // 下载完成后立即预初始化引擎，这样用户点击时直接可用
                    ensureEngine()
                } else {
                    postToJs("window.__onVoiceError('模型下载失败，请检查网络后重试')")
                }
            } catch (e: Exception) {
                Log.e(TAG, "下载流程异常", e)
                postToJs("window.__onVoiceError('${escapeJs(e.message ?: "未知错误")}')")
            }
        }
    }

    /** 释放识别引擎资源 */
    private fun releaseEngine() {
        initialized = false
        engineInitJob?.cancel()
        engineInitJob = null
        try { recognizer?.release() } catch (_: Exception) {}
        recognizer = null
        try { vad?.release() } catch (_: Exception) {}
        vad = null
    }

    /** 预初始化引擎（后台，不阻塞调用者）。下载完成后或 App 启动时调用 */
    fun ensureEngine() {
        if (initialized) return
        if (!isModelReady()) {
            logToFile("ensureEngine: 模型未就绪，跳过")
            return
        }
        if (engineInitJob?.isActive == true) {
            logToFile("ensureEngine: 已有初始化任务运行中")
            return
        }
        logToFile("ensureEngine: 开始后台预初始化引擎")
        engineInitJob = scope.launch(Dispatchers.IO) {
            val ok = initEngine()
            logToFile("ensureEngine: initEngine 返回 $ok")
            if (ok) {
                withContext(Dispatchers.Main) {
                    postToJs("window.__onVoiceModelReady()")
                }
            }
        }
    }

    /** 获取模型大小（用于 UI 展示） */
    @JavascriptInterface
    fun getModelSizeMB(): Double {
        return 228.0
    }

    /** 收集调试日志并上传到服务器 */
    @JavascriptInterface
    fun debugLog(msg: String) {
        logToFile("[JS] $msg")
    }

    @JavascriptInterface
    fun collectDebugLog(): String {
        val sb = StringBuilder()
        sb.appendLine("=== 语音调试日志 ===")
        sb.appendLine("时间: ${dateFmt.format(Date())}")
        sb.appendLine("模型目录: ${modelDir.absolutePath}")
        sb.appendLine("senseVoice model: 存在=${senseVoiceModel.exists()}, 大小=${if (senseVoiceModel.exists()) senseVoiceModel.length() else -1}")
        sb.appendLine("senseVoice tokens: 存在=${senseVoiceTokens.exists()}, 大小=${if (senseVoiceTokens.exists()) senseVoiceTokens.length() else -1}")
        sb.appendLine("sileroVad: 存在=${sileroVadModel.exists()}, 大小=${if (sileroVadModel.exists()) sileroVadModel.length() else -1}")
        sb.appendLine("isModelReady: ${ModelManager.isModelReady(context)}")
        sb.appendLine("引擎已初始化: $initialized")
        sb.appendLine("recognizer: ${recognizer != null}, vad: ${vad != null}")
        sb.appendLine("录音权限: ${hasRecordPermission()}")
        sb.appendLine("isRecording: ${isRecording.get()}")
        sb.appendLine("")
        sb.appendLine("=== 文件日志 ===")
        if (logFile.exists()) {
            try { sb.append(logFile.readText()) } catch (e: Exception) { sb.appendLine("(读取日志失败: ${e.message})") }
        } else {
            sb.appendLine("(无文件日志)")
        }
        return sb.toString()
    }

    @JavascriptInterface
    fun startListening() {
        logToFile("startListening 被调用, initialized=$initialized")
        if (!hasRecordPermission()) {
            logToFile("startListening: 缺少录音权限")
            postToJs("window.__onVoiceError('缺少录音权限')")
            Log.e(TAG, "缺少录音权限")
            return
        }

        // 检查模型是否就绪
        if (!isModelReady()) {
            logToFile("startListening: 模型未就绪")
            postToJs("window.__onVoiceStatus('model_not_ready')")
            Log.w(TAG, "模型未就绪")
            return
        }

        if (isRecording.get()) {
            logToFile("startListening: 已在录音中，忽略")
            Log.w(TAG, "已经在录音中")
            return
        }

        if (!initialized) {
            // 触发后台初始化并等待，完成后自动开始录音
            logToFile("startListening: 引擎未初始化，触发后台初始化并等待")
            postToJs("window.__onVoiceStatus('initializing')")
            pendingStartJob?.cancel()
            pendingStartJob = scope.launch(Dispatchers.IO) {
                val ok = initEngine()
                logToFile("startListening: initEngine 返回 $ok")
                withContext(Dispatchers.Main) {
                    if (pendingStartJob == null || !pendingStartJob!!.isActive) {
                        logToFile("startListening: 启动请求在初始化期间被取消")
                        return@withContext
                    }
                    pendingStartJob = null
                    if (!ok) {
                        postToJs("window.__onVoiceError('模型初始化失败')")
                        return@withContext
                    }
                    startRecordingInternal()
                }
            }
        } else {
            startRecordingInternal()
        }
    }

    @JavascriptInterface
    fun stopListening() {
        logToFile("stopListening 被调用, isRecording=${isRecording.get()}")

        // 取消待处理的启动（初始化期间的取消）
        pendingStartJob?.cancel()
        pendingStartJob = null

        // 如果还没开始录音（初始化中），直接通知前端停止
        if (!isRecording.get()) {
            logToFile("stopListening: 录音尚未开始，取消启动请求")
            postToJs("window.__onVoiceStatus('idle')")
            return
        }

        // 防抖：录音开始后 MIN_RECORDING_MS 内不允许停止
        val elapsed = System.currentTimeMillis() - recordingStartTime
        if (elapsed < MIN_RECORDING_MS) {
            logToFile("stopListening: 录音时长不足 ${MIN_RECORDING_MS}ms，忽略 (已录 ${elapsed}ms)")
            Log.w(TAG, "录音时长不足 ${MIN_RECORDING_MS}ms，忽略停止请求 (已录 ${elapsed}ms)")
            return
        }
        stopRecordingInternal()
    }

    // ═══════════════════ 引擎初始化 ═══════════════════

    private suspend fun initEngine(): Boolean = withContext(Dispatchers.IO) {
        try {
            // 先做文件大小校验，避免用残缺文件初始化导致 Native crash
            if (!ModelManager.isModelReady(context)) {
                logToFile("initEngine: 模型文件不完整，拒绝初始化")
                Log.e(TAG, "模型文件不完整，拒绝初始化")
                return@withContext false
            }

            logToFile("initEngine: 模型文件完整，开始加载 SenseVoice...")
            Log.i(TAG, "初始化 SenseVoice 识别器... model=${senseVoiceModel.length()} tokens=${senseVoiceTokens.length()}")

            // SenseVoice 模型配置
            val senseVoiceConfig = OfflineSenseVoiceModelConfig(
                model = senseVoiceModel.absolutePath,
                useInverseTextNormalization = true
            )

            val featConfig = FeatureConfig(
                sampleRate = SAMPLE_RATE,
                featureDim = 80
            )

            val modelConfig = OfflineModelConfig()
            modelConfig.senseVoice = senseVoiceConfig
            modelConfig.tokens = senseVoiceTokens.absolutePath
            modelConfig.numThreads = 1  // 单线程降低内存压力
            modelConfig.provider = "cpu"

            val config = OfflineRecognizerConfig(
                featConfig = featConfig,
                modelConfig = modelConfig
            )

            Log.i(TAG, "开始创建 OfflineRecognizer...")
            logToFile("initEngine: 开始创建 OfflineRecognizer (numThreads=1)...")
            recognizer = OfflineRecognizer(
                assetManager = null,
                config = config
            )
            logToFile("initEngine: OfflineRecognizer 创建完成")
            Log.i(TAG, "SenseVoice 识别器初始化完成")

            // VAD 暂时禁用（Android 端 Silero VAD 存在兼容性问题，改用整段识别）
            logToFile("initEngine: 跳过 VAD，使用整段识别模式")
            vad = null

            initialized = true
            logToFile("initEngine: 全部初始化完成")
            true
        } catch (e: Exception) {
            logToFile("initEngine: 异常 ${e.javaClass.simpleName}: ${e.message}")
            false
        }
    }

    // ═══════════════════ 录音 ═══════════════════

    // ── 待处理的启动任务（用于在初始化期间取消）──
    private var pendingStartJob: Job? = null

    // ── 录音数据缓冲（无 VAD 模式，整段识别）──
    private val audioBuffer = mutableListOf<Short>()

    private fun startRecordingInternal() {
        logToFile("startRecordingInternal: 开始, isRecording=${isRecording.get()}")
        if (isRecording.getAndSet(true)) {
            logToFile("startRecordingInternal: 已在录音中，跳过")
            return
        }

        val bufferSize = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        logToFile("startRecordingInternal: bufferSize=$bufferSize")

        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize * 2
        )

        if (audioRecord?.state != AudioRecord.STATE_INITIALIZED) {
            logToFile("startRecordingInternal: AudioRecord 初始化失败 state=${audioRecord?.state}")
            Log.e(TAG, "AudioRecord 初始化失败")
            isRecording.set(false)
            postToJs("window.__onVoiceError('麦克风初始化失败')")
            return
        }

        audioRecord?.startRecording()
        recordingStartTime = System.currentTimeMillis()
        audioBuffer.clear()
        postToJs("window.__onVoiceStatus('listening')")
        logToFile("startRecordingInternal: 录音已开始")
        Log.i(TAG, "开始录音")

        recordingJob = scope.launch {
            processAudioLoop()
        }
    }

    private suspend fun processAudioLoop() {
        val buffer = ShortArray(CHUNK_SIZE)
        while (isRecording.get()) {
            val readCount = audioRecord?.read(buffer, 0, buffer.size) ?: -1
            if (readCount <= 0) continue
            for (i in 0 until readCount) {
                audioBuffer.add(buffer[i])
            }
        }
    }

    private fun recognizeFullAudio() {
        logToFile("recognizeFullAudio: buffer size=${audioBuffer.size}")
        if (audioBuffer.isEmpty()) {
            logToFile("recognizeFullAudio: buffer 为空")
            postToJs("window.__onVoiceError('未检测到语音')")
            return
        }
        scope.launch {
            postToJs("window.__onVoiceStatus('processing')")
            val samples = FloatArray(audioBuffer.size) { audioBuffer[it] / 32768.0f }
            logToFile("recognizeFullAudio: 开始识别 ${samples.size} 采样 (${samples.size / SAMPLE_RATE}s)")
            val text = withContext(Dispatchers.IO) { recognizeSegment(samples) }
            logToFile("recognizeFullAudio: 识别结果 text='$text' length=${text.length}")
            if (text.isNotBlank()) {
                Log.i(TAG, "识别结果: $text")
                logToFile("recognizeFullAudio: 准备调用 postToJs __onVoiceResult")
                postToJs("window.__onVoiceResult('${escapeJs(text)}')")
                logToFile("recognizeFullAudio: postToJs __onVoiceResult 已提交")
            } else {
                logToFile("recognizeFullAudio: 识别结果为空")
                postToJs("window.__onVoiceError('未识别到语音内容')")
            }
            postToJs("window.__onVoiceStatus('idle')")
        }
    }

    private fun recognizeSegment(samples: FloatArray): String {
        val rec = recognizer ?: return ""
        return try {
            val stream = rec.createStream()
            stream.acceptWaveform(samples, SAMPLE_RATE)
            rec.decode(stream)
            val result = rec.getResult(stream)
            stream.release()
            val text = result.text?.trim() ?: ""
            logToFile("recognizeSegment: text='$text' lang='${result.lang}' emotion='${result.emotion}' event='${result.event}'")
            text
        } catch (e: Exception) {
            logToFile("recognizeSegment: 异常 ${e.javaClass.simpleName}: ${e.message}")
            Log.e(TAG, "识别错误", e)
            ""
        }
    }

    private fun stopRecordingInternal() {
        logToFile("stopRecordingInternal 被调用, isRecording=${isRecording.get()}")
        if (!isRecording.getAndSet(false)) {
            logToFile("stopRecordingInternal: isRecording 已为 false，跳过")
            return
        }

        recordingJob?.cancel()
        recordingJob = null

        try {
            audioRecord?.stop()
            audioRecord?.release()
        } catch (e: Exception) {
            Log.w(TAG, "AudioRecord 释放异常", e)
            logToFile("stopRecordingInternal: AudioRecord 释放异常 ${e.message}")
        }
        audioRecord = null

        logToFile("stopRecordingInternal: 录音已停止，共 ${audioBuffer.size} 采样")
        Log.i(TAG, "录音已停止，共 ${audioBuffer.size} 采样")
        recognizeFullAudio()
    }

    // ═══════════════════ 工具方法 ═══════════════════

    private fun hasRecordPermission(): Boolean {
        return ActivityCompat.checkSelfPermission(
            context, Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun postToJs(script: String) {
        webView.post {
            webView.evaluateJavascript(script, null)
        }
    }

    private fun escapeJs(text: String): String {
        return text
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
    }

    fun destroy() {
        stopRecordingInternal()
        scope.cancel()
        releaseEngine()
    }
}
