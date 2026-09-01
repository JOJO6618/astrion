package com.cyjai.agent

import android.content.Context
import android.util.Log
import kotlinx.coroutines.*
import java.io.*
import java.net.HttpURLConnection
import java.net.URL

/**
 * 语音模型下载管理器
 * 首次启动时自动下载 SenseVoice + Silero VAD 模型到内部存储
 *
 * 模型来源：HuggingFace (csukuangfj)
 * 总大小：约 230MB（SenseVoice int8 228MB + tokens 308KB + Silero VAD 1.5MB）
 */
object ModelManager {
    private const val TAG = "ModelManager"

    // 模型下载地址（从自有服务器下载，3 个文件约 230MB）
    // 需提前上传到服务器静态目录：/static/voice_models/
    private val BASE_URL = BuildConfig.HOME_URL.trimEnd('/') + "/static/voice_models"
    private val SENSEVOICE_MODEL_URL = "$BASE_URL/model.int8.onnx"
    private val SENSEVOICE_TOKENS_URL = "$BASE_URL/tokens.txt"
    private val SILERO_VAD_URL = "$BASE_URL/silero_vad.onnx"

    private var _modelDir: File? = null

    fun init(context: Context) {
        _modelDir = File(context.filesDir, "voice_models")
    }

    private fun getModelDir(context: Context): File {
        return _modelDir ?: File(context.filesDir, "voice_models").also { _modelDir = it }
    }

    // 模型文件预期大小（字节），用于校验下载完整性（±5% 容差）
    private const val EXPECTED_MODEL_SIZE = 239_233_841L  // model.int8.onnx ~228MB
    private const val EXPECTED_TOKENS_SIZE = 316_000L      // tokens.txt ~308KB
    private const val EXPECTED_VAD_SIZE = 644_000L         // silero_vad.onnx ~629KB
    private const val SIZE_TOLERANCE = 0.05                // 5% 容差

    private fun isSizeValid(file: File, expected: Long): Boolean {
        if (!file.exists()) return false
        val size = file.length()
        val lower = (expected * (1.0 - SIZE_TOLERANCE)).toLong()
        val upper = (expected * (1.0 + SIZE_TOLERANCE)).toLong()
        return size in lower..upper
    }

    /**
     * 检查模型是否已下载完毕且文件完整
     */
    fun isModelReady(context: Context): Boolean {
        val dir = getModelDir(context)
        val senseVoiceModel = File(dir, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/model.int8.onnx")
        val senseVoiceTokens = File(dir, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/tokens.txt")
        val sileroVad = File(dir, "silero_vad.onnx")
        return isSizeValid(senseVoiceModel, EXPECTED_MODEL_SIZE)
                && isSizeValid(senseVoiceTokens, EXPECTED_TOKENS_SIZE)
                && isSizeValid(sileroVad, EXPECTED_VAD_SIZE)
    }

    /**
     * 删除所有已下载的模型文件
     */
    fun deleteModels(context: Context): Boolean {
        val dir = getModelDir(context)
        return try {
            dir.deleteRecursively()
        } catch (e: Exception) {
            Log.e(TAG, "删除模型文件失败", e)
            false
        }
    }

    /**
     * 下载所有模型（应在后台线程调用）
     * @param onProgress 进度回调 (总百分比: Int, 阶段描述: String)
     * @return 是否全部成功
     */
    suspend fun downloadModels(
        context: Context,
        onProgress: (Int, String) -> Unit = { _, _ -> }
    ): Boolean = withContext(Dispatchers.IO) {
        val dir = getModelDir(context)
        val senseVoiceDir = File(dir, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17")
        senseVoiceDir.mkdirs()

        try {
            // 1. Silero VAD (~1.5MB)
            val sileroFile = File(dir, "silero_vad.onnx")
            if (!sileroFile.exists()) {
                onProgress(0, "下载 VAD 模型...")
                downloadFile(SILERO_VAD_URL, sileroFile) { pct ->
                    onProgress(pct * 5 / 100, "下载 VAD 模型... ${pct}%")
                }
            }

            // 2. tokens.txt (~308KB)
            val tokensFile = File(senseVoiceDir, "tokens.txt")
            if (!tokensFile.exists()) {
                onProgress(5, "下载 tokens...")
                downloadFile(SENSEVOICE_TOKENS_URL, tokensFile) { pct ->
                    onProgress(5 + pct * 2 / 100, "下载 tokens... ${pct}%")
                }
            }

            // 3. SenseVoice int8 (~228MB)
            val modelFile = File(senseVoiceDir, "model.int8.onnx")
            if (!modelFile.exists()) {
                onProgress(7, "下载 SenseVoice 模型（约 228MB）...")
                downloadFile(SENSEVOICE_MODEL_URL, modelFile) { pct ->
                    onProgress(7 + pct * 93 / 100, "下载 SenseVoice 模型... ${pct}%")
                }
            }

            onProgress(100, "模型就绪")
            Log.i(TAG, "模型下载完成")
            true
        } catch (e: Exception) {
            Log.e(TAG, "模型下载失败", e)
            false
        }
    }

    private fun downloadFile(urlStr: String, dest: File, onProgress: (Int) -> Unit) {
        var attempt = 0
        val maxAttempts = 3

        while (attempt < maxAttempts) {
            try {
                attempt++
                doDownload(urlStr, dest, onProgress)
                return
            } catch (e: Exception) {
                Log.w(TAG, "下载失败 (第${attempt}次): ${dest.name}", e)
                if (attempt >= maxAttempts) throw e
                Thread.sleep(2000) // 重试前等待
            }
        }
    }

    private fun doDownload(urlStr: String, dest: File, onProgress: (Int) -> Unit) {
        val url = URL(urlStr)
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.connectTimeout = 15000
        conn.readTimeout = 120000

        // 断点续传
        var downloaded = if (dest.exists()) dest.length() else 0L
        if (downloaded > 0) {
            conn.setRequestProperty("Range", "bytes=$downloaded-")
        }

        conn.connect()

        val totalSize = if (downloaded > 0) {
            conn.getHeaderField("Content-Range")?.substringAfter("/")?.toLongOrNull()
                ?: (conn.contentLengthLong + downloaded)
        } else {
            conn.contentLengthLong
        }

        val inputStream = conn.inputStream
        val outputStream = FileOutputStream(dest, downloaded > 0)
        val buffer = ByteArray(65536)
        var bytesRead: Int
        var totalRead = downloaded
        var lastProgressReport = System.currentTimeMillis()

        while (inputStream.read(buffer).also { bytesRead = it } != -1) {
            outputStream.write(buffer, 0, bytesRead)
            totalRead += bytesRead

            // 限流进度回调（每 200ms 最多报一次）
            val now = System.currentTimeMillis()
            if (totalSize > 0 && now - lastProgressReport > 200) {
                val pct = (totalRead * 100 / totalSize).toInt()
                onProgress(pct.coerceIn(0, 100))
                lastProgressReport = now
            }
        }

        if (totalSize > 0 && totalRead < totalSize) {
            throw IOException("下载不完整: 期望 ${totalSize} 字节，实际 ${totalRead} 字节")
        }

        inputStream.close()
        outputStream.close()
        conn.disconnect()
    }
}
