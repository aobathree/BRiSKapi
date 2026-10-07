//! Bounded local timing indicators. Source-clock origin is not established.
use serde::Serialize;
use std::collections::VecDeque;

const WINDOW: usize = 512;

#[derive(Debug, Default)]
struct Samples {
    values: VecDeque<f64>,
    count: u64,
    max: f64,
}

#[derive(Debug, Serialize)]
pub struct Distribution {
    pub last_ms: Option<f64>,
    pub p50_ms: Option<f64>,
    pub p95_ms: Option<f64>,
    pub p99_ms: Option<f64>,
    pub session_max_ms: Option<f64>,
    pub session_samples: u64,
    pub window_samples: usize,
}

impl Samples {
    fn push(&mut self, ms: f64) {
        if self.values.len() == WINDOW {
            self.values.pop_front();
        }
        self.values.push_back(ms);
        self.count += 1;
        self.max = self.max.max(ms);
    }

    fn summary(&self) -> Distribution {
        let mut sorted: Vec<_> = self.values.iter().copied().collect();
        sorted.sort_by(f64::total_cmp);
        let percentile = |percent: usize| {
            if sorted.is_empty() {
                None
            } else {
                Some(sorted[(sorted.len() * percent).div_ceil(100) - 1])
            }
        };
        Distribution {
            last_ms: self.values.back().copied(),
            p50_ms: percentile(50),
            p95_ms: percentile(95),
            p99_ms: percentile(99),
            session_max_ms: (self.count > 0).then_some(self.max),
            session_samples: self.count,
            window_samples: self.values.len(),
        }
    }
}

#[derive(Debug, Default)]
pub struct Latency {
    decode: Samples,
    local_pipeline: Samples,
    replay_schedule: Samples,
    clock_anomalies: u64,
    missing_receipt_times: u64,
}

#[derive(Debug, Serialize)]
pub struct LatencySummary {
    pub exchange_delay_ms: Option<f64>,
    pub exchange_delay_status: &'static str,
    pub source_timestamp_origin: &'static str,
    pub percentile_window_limit: usize,
    pub decode: Distribution,
    /// Node mock receipt through JSON transfer/queue and Rust parse/apply.
    /// Same-host wall clocks; receipt time has millisecond precision.
    pub local_receive_to_state: Distribution,
    /// Monotonic delay against the paced replay's target receipt schedule.
    pub replay_schedule_lateness: Distribution,
    pub local_clock_anomalies: u64,
    pub missing_receipt_times: u64,
}

impl Latency {
    pub fn record(
        &mut self,
        decode_ns: u64,
        received_unix_ms: Option<u64>,
        state_ready_unix_ms: u64,
        replay_lateness_ms: Option<f64>,
    ) {
        self.decode.push(decode_ns as f64 / 1_000_000.0);
        match received_unix_ms {
            Some(received) if received <= state_ready_unix_ms => {
                self.local_pipeline
                    .push((state_ready_unix_ms - received) as f64);
            }
            Some(_) => self.clock_anomalies += 1,
            None => self.missing_receipt_times += 1,
        }
        if let Some(ms) = replay_lateness_ms.filter(|ms| ms.is_finite() && *ms >= 0.0) {
            self.replay_schedule.push(ms);
        }
    }

    pub fn summary(&self) -> LatencySummary {
        LatencySummary {
            exchange_delay_ms: None,
            exchange_delay_status: "unavailable_historical_mock_and_unverified_timestamp_origin",
            source_timestamp_origin: "brisk_decoder_unverified",
            percentile_window_limit: WINDOW,
            decode: self.decode.summary(),
            local_receive_to_state: self.local_pipeline.summary(),
            replay_schedule_lateness: self.replay_schedule.summary(),
            local_clock_anomalies: self.clock_anomalies,
            missing_receipt_times: self.missing_receipt_times,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn distinguishes_local_metrics_and_unavailable_exchange_latency() {
        let mut latency = Latency::default();
        assert_eq!(latency.summary().decode.p99_ms, None);
        for ms in 1..=100 {
            latency.record(ms * 1_000_000, Some(1000), 1000 + ms, Some(ms as f64 / 2.0));
        }
        let s = latency.summary();
        assert_eq!(s.exchange_delay_ms, None);
        assert_eq!(s.decode.last_ms, Some(100.0));
        assert_eq!(s.decode.p50_ms, Some(50.0));
        assert_eq!(s.decode.p95_ms, Some(95.0));
        assert_eq!(s.decode.p99_ms, Some(99.0));
        assert_eq!(s.local_receive_to_state.p99_ms, Some(99.0));
        assert_eq!(s.replay_schedule_lateness.p50_ms, Some(25.0));
    }

    #[test]
    fn bounded_window_preserves_session_max_and_handles_clock_errors() {
        let mut latency = Latency::default();
        latency.record(999_000_000, Some(0), 1000, None);
        for _ in 0..600 {
            latency.record(1_000_000, Some(1000), 1001, None);
        }
        latency.record(0, Some(2000), 1000, Some(-1.0));
        latency.record(0, None, 1000, Some(f64::NAN));
        let s = latency.summary();
        assert_eq!(s.decode.window_samples, WINDOW);
        assert_eq!(s.decode.session_samples, 603);
        assert_eq!(s.decode.p99_ms, Some(1.0));
        assert_eq!(s.decode.session_max_ms, Some(999.0));
        assert_eq!(s.local_receive_to_state.session_max_ms, Some(1000.0));
        assert_eq!(s.local_clock_anomalies, 1);
        assert_eq!(s.missing_receipt_times, 1);
        assert_eq!(s.replay_schedule_lateness.session_samples, 0);
    }
}
