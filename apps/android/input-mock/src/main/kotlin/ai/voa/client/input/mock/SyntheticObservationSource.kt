package ai.voa.client.input.mock

// 에뮬레이터 E2E 검증을 위한 합성 관측 공급자 경계 (AND-06)
interface SyntheticObservationSource {
    val streamId: String
}

