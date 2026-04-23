#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MossError {
    InvalidVectorDimension,
    MissingDocument,
    SerializationUnavailable,
    SerializationFailed,
    DeserializationFailed,
}

