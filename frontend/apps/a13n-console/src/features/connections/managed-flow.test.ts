import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { expect, test, vi } from "vitest";
import {
  saveManagedSelector,
  takeManagedSelector,
  managedFlowKey,
} from "./managed-flow";
import { managedSetupSchema } from "./managed-schema";

const html = readFileSync(
  new URL("../../../index.html", import.meta.url),
  "utf8",
);
const capture = html.match(/<script>([\s\S]*?)<\/script>/)![1];

test.each([
  ["?session_uri=private%3A%2F%2Fonce", "private://once", false],
  ["?session_uri=one&session_uri=two", null, true],
  ["?session_uri=one&account=swapped", null, true],
  ["?session_uri=one#copy", null, true],
  ["", null, false],
  ["?status=success&connected_account_id=ca_untrusted", null, false],
  ["?status=failed&connected_account_id=ca_untrusted", null, true],
  ["?status=success&status=failed", null, true],
  ["?session_uri=one&status=success", null, true],
])(
  "document removes callback before any application module: %s",
  (suffix, value, invalid) => {
    const calls: string[] = [];
    const target = {
      pathname: "/managed/verify",
      href: "https://console.test/managed/verify" + suffix,
    };
    const window: Record<string, unknown> = {};
    runInNewContext(capture, {
      URL,
      location: target,
      window,
      history: {
        replaceState: (_state: unknown, _unused: string, path: string) =>
          calls.push(path),
      },
    });
    expect(calls).toEqual(["/managed/verify"]);
    expect(window.__a13nManagedCallback).toEqual({
      sessionUri: value,
      invalid,
    });
    expect(html.indexOf('name="referrer"')).toBeLessThan(
      html.indexOf("<script>"),
    );
    expect(html.indexOf("<script>")).toBeLessThan(
      html.indexOf('type="module"'),
    );
  },
);

test("selector storage is non-secret and single-use in its own tab", () => {
  const values = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  });
  const selector = {
    workspaceId: "workspace",
    connectionId: "conn",
    authorizationId: "cauth",
    generation: 3,
  };
  saveManagedSelector(selector);
  expect(values.get(managedFlowKey)).toBe(JSON.stringify(selector));
  expect(takeManagedSelector()).toEqual(selector);
  expect(takeManagedSelector()).toBeNull();
  values.set(managedFlowKey, '{"generation":-1}');
  expect(takeManagedSelector()).toBeNull();
  vi.unstubAllGlobals();
});

test("selected auth configuration shows its required non-secret instance fields", () => {
  const instance = {
    type: "object",
    properties: { region: { type: "string" } },
    required: ["region"],
  };
  const schema = {
    properties: { connection_data: { type: "object" } },
    allOf: [
      {
        if: { properties: { auth_config_id: { enum: ["ac_region"] } } },
        then: { properties: { connection_data: instance } },
      },
    ],
  };
  expect(managedSetupSchema(schema, "ac_region").properties).toEqual({
    connection_data: instance,
  });
  expect(managedSetupSchema(schema, "ac_other")).toEqual(schema);
});
