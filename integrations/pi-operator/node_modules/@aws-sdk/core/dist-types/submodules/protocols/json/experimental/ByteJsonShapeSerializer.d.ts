import type { Schema, ShapeSerializer } from "@smithy/types";
import { SerdeContextConfig } from "../../ConfigurableSerdeContext";
import type { JsonSettings } from "../JsonCodec";
/**
 * Experimental single-pass JSON serializer that writes directly to a Uint8Array buffer.
 * Fewer intermediate states as when compared to the initial multi-pass implementation.
 *
 * @internal
 */
export declare class ByteJsonShapeSerializer extends SerdeContextConfig implements ShapeSerializer<Uint8Array> {
    readonly settings: JsonSettings;
    private json;
    private i;
    private rootSchema;
    constructor(settings: JsonSettings);
    write(schema: Schema, value: unknown): void;
    /**
     * @internal
     */
    writeDiscriminatedDocument(schema: Schema, value: unknown): void;
    /**
     * Returns the serialized JSON as a Uint8Array (UTF-8 bytes).
     * This is the primary output — pass directly to request.body.
     */
    flush(): Uint8Array;
    private ensure;
    /**
     * Write a raw ASCII string (no JSON escaping). Used for pre-validated content
     * like numeric literals and pre-encoded base64.
     */
    private writeAscii;
    /**
     * Write a quoted ASCII string with no escape checking.
     * Used for struct member keys (jsonName or model names) which are
     * guaranteed to be safe ASCII identifiers. No control chars, quotes,
     * backslashes, or non-ASCII.
     * Ensures extra room for surrounding structural chars (comma, colon).
     */
    private writeAsciiQuoted;
    /**
     * Write a JSON-escaped string including the surrounding quotes.
     * Fast-path for ASCII, falls back to TextEncoder for multi-byte.
     */
    private writeJsonString;
    private writeUnicodeEscape;
    private static readonly B64;
    /**
     * Write a Uint8Array as a quoted base64 string directly into the buffer.
     * No intermediate JS string, no escape checking (base64 alphabet is safe ASCII).
     */
    private writeBase64;
    private writeValue;
    private writeStruct;
    private writeList;
    private writeMap;
    private writeTimestamp;
}
