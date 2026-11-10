package ai.voa.client.input.media

// Stage C/D 카메라, IMU, 마이크, PTT 입력 어댑터 경계 (AND-07, AND-08)
interface MediaCaptureSource {
    val streamId: String
    fun stopImmediate()
}

