package ai.voa.client.testing

// Watchdog 및 재연결 경계값 검증을 위한 결정론적 가짜 단조 시계
class FakeMonotonicClock(
    initialMs: Long = 0L,
) {
    var currentMs: Long = initialMs
        private set

    fun advanceBy(deltaMs: Long) {
        require(deltaMs >= 0L) { "deltaMs must be non-negative" }
        currentMs += deltaMs
    }
}

