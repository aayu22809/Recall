/// <reference types="@raycast/api">

/* 🚧 🚧 🚧
 * This file is auto-generated from the extension's manifest.
 * Do not modify manually. Instead, update the `package.json` file.
 * 🚧 🚧 🚧 */

/* eslint-disable @typescript-eslint/ban-types */

type ExtensionPreferences = {
  /** Python Package Path - Absolute path to the vector-embedded-finder Python package (the directory containing vector_embedded_finder/) */
  "pythonPackagePath": string,
  /** Python Binary - Path to the python3 binary (leave blank for default) */
  "pythonPath": string
}

/** Preferences accessible in all the extension's commands */
declare type Preferences = ExtensionPreferences

declare namespace Preferences {
  /** Preferences accessible in the `search-memory` command */
  export type SearchMemory = ExtensionPreferences & {}
  /** Preferences accessible in the `open-memory` command */
  export type OpenMemory = ExtensionPreferences & {}
}

declare namespace Arguments {
  /** Arguments passed to the `search-memory` command */
  export type SearchMemory = {}
  /** Arguments passed to the `open-memory` command */
  export type OpenMemory = {
  /** Search query */
  "query": string
}
}

