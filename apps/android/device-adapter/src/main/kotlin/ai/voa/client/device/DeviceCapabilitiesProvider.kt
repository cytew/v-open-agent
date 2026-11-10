package ai.voa.client.device

import ai.voa.client.protocol.InputCap
import ai.voa.client.protocol.OutputCap

// 특정 글래스 SDK 타입이 앱 코어로 퍼지지 않도록 분리하는 기기 어댑터 경계
interface DeviceCapabilitiesProvider {
    val supportedInputs: Set<InputCap>
    val supportedOutputs: Set<OutputCap>
}

