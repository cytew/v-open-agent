package ai.voa.client.session

// 세션 상태 모델 (05-android-client-plan.md 5절)
enum class SessionState {
    STOPPED,
    PROVISIONED,
    SESSION_CREATING,
    WS_AUTHENTICATING,
    ACTIVE,
    DEGRADED,
    RECONNECTING,
}

// 벽시계 변경에 영향받지 않는 단조 시계 포트
fun interface MonotonicClock {
    fun nowMs(): Long
}

