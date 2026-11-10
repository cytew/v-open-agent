package ai.voa.client.protocol

import kotlinx.serialization.SerializationException
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class ProtocolContractTest {
    @Test
    fun `capability and risk state enums serialize to spec literal values`() {
        assertEquals("\"camera_0\"", ProtocolContract.json.encodeToString(InputCap.CAMERA_0))
        assertEquals("\"hud\"", ProtocolContract.json.encodeToString(OutputCap.HUD))
        assertEquals("\"warning\"", ProtocolContract.json.encodeToString(RiskState.WARNING))
        assertEquals("\"played\"", ProtocolContract.json.encodeToString(AckStatus.PLAYED))
    }

    @Test
    fun `unknown enum literal is rejected`() {
        assertFailsWith<SerializationException> {
            ProtocolContract.json.decodeFromString<InputCap>("\"lidar_0\"")
        }
    }
}

