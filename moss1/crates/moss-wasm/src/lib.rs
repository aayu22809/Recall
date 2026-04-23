use std::cell::RefCell;
use std::collections::BTreeMap;

use moss_core::{MossConfig, MossCore, SearchOptions};
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub struct MossWasmClient {
    inner: RefCell<MossCore>,
}

#[wasm_bindgen]
impl MossWasmClient {
    #[wasm_bindgen(constructor)]
    pub fn new() -> Self {
        Self {
            inner: RefCell::new(MossCore::new(MossConfig::default())),
        }
    }

    pub fn insert(
        &self,
        id: String,
        content: String,
        namespace: String,
        embedding: Vec<f32>,
    ) -> Result<(), JsValue> {
        self.inner
            .borrow_mut()
            .insert(id, content, embedding, namespace, BTreeMap::new(), 0)
            .map_err(|e| JsValue::from_str(&format!("{e:?}")))
    }

    pub fn delete(&self, id: String) -> Result<(), JsValue> {
        self.inner
            .borrow_mut()
            .delete(&id)
            .map_err(|e| JsValue::from_str(&format!("{e:?}")))
    }

    pub fn search(
        &self,
        query_embedding: Vec<f32>,
        top_k: usize,
        namespace: Option<String>,
    ) -> Result<JsValue, JsValue> {
        let options = SearchOptions {
            top_k,
            namespace,
            ..SearchOptions::default()
        };
        let results = self.inner.borrow_mut().search(&query_embedding, &options);
        serde_wasm_bindgen::to_value(&results).map_err(|e| JsValue::from_str(&e.to_string()))
    }

    pub fn compact(&self) {
        self.inner.borrow_mut().compact();
    }

    pub fn export_snapshot(&self) -> Result<Vec<u8>, JsValue> {
        self.inner
            .borrow()
            .export_snapshot()
            .map_err(|e| JsValue::from_str(&format!("{e:?}")))
    }

    pub fn import_snapshot(&self, snapshot: Vec<u8>) -> Result<(), JsValue> {
        self.inner
            .borrow_mut()
            .import_snapshot(&snapshot)
            .map_err(|e| JsValue::from_str(&format!("{e:?}")))
    }
}

impl Default for MossWasmClient {
    fn default() -> Self {
        Self::new()
    }
}

