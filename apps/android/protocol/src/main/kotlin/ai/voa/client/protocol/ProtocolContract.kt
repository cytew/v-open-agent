package ai.voa.client.protocol

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

// 서버와 공유하는 스키마 버전 및 기본 JSON 직렬화 설정 (SPEC 3, 7)
object ProtocolContract {
    const val SCHEMA_VERSION: String = "0.1.0"

    val json: Json = Json {
        // 알 수 없는 필드는 엄격히 거부한다 (SPEC 7.2 extra="forbid")
        ignoreUnknownKeys = false
        encodeDefaults = true
        explicitNulls = false
    }
}

@Serializable
enum class InputCap {
    @SerialName("camera_0")
    CAMERA_0,

    @SerialName("mic_0")
    MIC_0,

    @SerialName("imu_0")
    IMU_0,

    @SerialName("ptt_0")
    PTT_0,
}

@Serializable
enum class OutputCap {
    @SerialName("hud")
    HUD,

    @SerialName("speaker")
    SPEAKER,

    @SerialName("haptic")
    HAPTIC,
}

@Serializable
enum class RiskState {
    @SerialName("clear")
    CLEAR,

    @SerialName("candidate")
    CANDIDATE,

    @SerialName("warning")
    WARNING,

    @SerialName("unknown")
    UNKNOWN,

    @SerialName("resolved")
    RESOLVED,
}

@Serializable
enum class AckStatus {
    @SerialName("received")
    RECEIVED,

    @SerialName("played")
    PLAYED,
}

