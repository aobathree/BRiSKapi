//! In-process state for the Nautilus v2 actor. No file polling or second Rust process.
use crate::{Batch, Quote, State};
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyList},
};
use serde_json::Value;
use std::time::{SystemTime, UNIX_EPOCH};

fn value_to_py(py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
    Ok(match value {
        Value::Null => py.None(),
        Value::Bool(v) => v.into_pyobject(py)?.to_owned().into_any().unbind(),
        Value::Number(v) => {
            if let Some(n) = v.as_u64() {
                n.into_pyobject(py)?.into_any().unbind()
            } else if let Some(n) = v.as_i64() {
                n.into_pyobject(py)?.into_any().unbind()
            } else {
                v.as_f64().unwrap().into_pyobject(py)?.into_any().unbind()
            }
        }
        Value::String(v) => v.into_pyobject(py)?.into_any().unbind(),
        Value::Array(values) => {
            let list = PyList::empty(py);
            for v in values {
                list.append(value_to_py(py, v)?)?;
            }
            list.into_any().unbind()
        }
        Value::Object(values) => {
            let dict = PyDict::new(py);
            for (k, v) in values {
                dict.set_item(k, value_to_py(py, v)?)?;
            }
            dict.into_any().unbind()
        }
    })
}

fn serialize_to_py(py: Python<'_>, value: &impl serde::Serialize) -> PyResult<Py<PyAny>> {
    let value = serde_json::to_value(value).map_err(|e| PyValueError::new_err(e.to_string()))?;
    value_to_py(py, &value)
}

fn quotes_to_py<'a>(
    py: Python<'_>,
    quotes: impl Iterator<Item = &'a Quote>,
) -> PyResult<Py<PyAny>> {
    let result = PyList::empty(py);
    for quote in quotes {
        let dict = PyDict::new(py);
        dict.set_item("issue_id", quote.issue_id)?;
        dict.set_item("code", &quote.code)?;
        dict.set_item("frame", quote.frame)?;
        dict.set_item("source_time_us", quote.source_time_us)?;
        for (key, value) in &quote.fields {
            dict.set_item(key, value_to_py(py, value)?)?;
        }
        result.append(dict)?;
    }
    Ok(result.into_any().unbind())
}

#[pyclass(name = "AuctionState", unsendable)]
#[derive(Default)]
struct AuctionState {
    state: State,
}

#[pymethods]
impl AuctionState {
    #[new]
    fn new() -> Self {
        Self::default()
    }

    /// Validate and apply the complete batch before exposing any updates.
    fn apply_json(&mut self, py: Python<'_>, line: &str) -> PyResult<Py<PyAny>> {
        let batch: Batch = serde_json::from_str(line).map_err(|e| {
            self.state
                .invalidate(format!("Malformed decoder batch: {e}"));
            PyValueError::new_err(e.to_string())
        })?;
        let kind = batch.r#type.clone();
        let seq = batch.seq;
        let quote_ids: Vec<_> = batch.quotes.iter().map(|q| q.issue_id).collect();
        let timing = (
            batch.decode_ns,
            batch.received_unix_ms,
            batch.replay_lateness_ms,
        );
        self.state
            .apply(batch)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        let ready_ns: u64 = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|e| PyValueError::new_err(e.to_string()))?
            .as_nanos()
            .try_into()
            .map_err(|_| PyValueError::new_err("Clock overflow"))?;
        if kind == "quotes" {
            self.state
                .latency
                .record(timing.0, timing.1, ready_ns / 1_000_000, timing.2);
        }
        let result = PyDict::new(py);
        result.set_item("type", kind)?;
        result.set_item("seq", seq)?;
        result.set_item("trading_date", &self.state.trading_date)?;
        result.set_item("source_time_us", self.state.source_time_us)?;
        result.set_item("received_unix_ms", timing.1)?;
        result.set_item("state_ready_ns", ready_ns)?;
        result.set_item(
            "quotes",
            quotes_to_py(py, quote_ids.iter().map(|id| &self.state.quotes[id]))?,
        )?;
        Ok(result.into_any().unbind())
    }

    #[pyo3(signature = (codes=None))]
    fn snapshot(&self, py: Python<'_>, codes: Option<Vec<String>>) -> PyResult<Py<PyAny>> {
        if !self.state.replay_running || self.state.failure.is_some() {
            return Err(PyValueError::new_err("No valid running state"));
        }
        let quotes = self
            .state
            .quotes
            .values()
            .filter(|q| codes.as_ref().is_none_or(|c| c.contains(&q.code)));
        quotes_to_py(py, quotes)
    }

    fn status(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let result = PyDict::new(py);
        result.set_item("replay_running", self.state.replay_running)?;
        result.set_item("failure", &self.state.failure)?;
        result.set_item(
            "input_transport",
            serialize_to_py(py, &self.state.input_transport)?,
        )?;
        result.set_item("trading_date", &self.state.trading_date)?;
        result.set_item("next_seq", self.state.next_seq)?;
        result.set_item("batches", self.state.batches)?;
        result.set_item("quote_updates", self.state.quote_updates)?;
        result.set_item("issues", self.state.quotes.len())?;
        result.set_item(
            "latency",
            serialize_to_py(py, &self.state.latency.summary())?,
        )?;
        Ok(result.into_any().unbind())
    }

    fn invalidate(&mut self, reason: String) {
        self.state.invalidate(reason);
    }
}

#[pymodule]
fn brisk_state_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<AuctionState>()
}
