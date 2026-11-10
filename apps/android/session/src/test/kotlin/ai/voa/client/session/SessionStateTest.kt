package ai.voa.client.session

import ai.voa.client.testing.FakeMonotonicClock
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class SessionStateTest {
    @Test
    fun `fake monotonic clock advances deterministically`() {
        val clock = FakeMonotonicClock(initialMs = 1_000L)
        val monotonicClock = MonotonicClock { clock.currentMs }

        assertEquals(1_000L, monotonicClock.nowMs())
        clock.advanceBy(2_500L)
        assertEquals(3_500L, monotonicClock.nowMs())
    }

    @Test
    fun `fake monotonic clock rejects negative delta`() {
        val clock = FakeMonotonicClock(initialMs = 1_000L)
        assertFailsWith<IllegalArgumentException> {
            clock.advanceBy(-1L)
        }
    }
}

