/**
 * The session's control socket: newline-delimited JSON requests in, one JSON
 * response per request out.
 */
import * as net from "node:net";

import type { Request, Respond } from "./api.ts";

interface Response {
  type: "response";
  command: string;
  success: boolean;
  data?: unknown;
  error?: string;
  id?: string;
}

export type RequestHandler = (request: Request, respond: Respond) => void | Promise<void>;

function write(socket: net.Socket, response: Response): void {
  try {
    socket.write(`${JSON.stringify(response)}\n`);
  } catch {
    // The client may have hung up.
  }
}

function parse(line: string): Request | string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch (error) {
    return error instanceof Error ? error.message : "Failed to parse command";
  }
  if (!parsed || typeof parsed !== "object") return "Invalid command";
  const request = parsed as Request;
  if (typeof request.type !== "string") return "Missing command type";
  return { ...request, id: typeof request.id === "string" ? request.id : undefined };
}

function onLine(socket: net.Socket, line: string, handle: RequestHandler): void {
  const request = parse(line);
  if (typeof request === "string") {
    write(socket, {
      type: "response",
      command: "parse",
      success: false,
      error: `Failed to parse command: ${request}`,
    });
    return;
  }
  const respond: Respond = (success, data, error) => {
    write(socket, { type: "response", command: request.type, success, data, error, id: request.id });
  };
  Promise.resolve()
    .then(() => handle(request, respond))
    .catch((error) => respond(false, undefined, error instanceof Error ? error.message : String(error)));
}

export async function listen(socketPath: string, handle: RequestHandler): Promise<net.Server> {
  const server = net.createServer((socket) => {
    socket.setEncoding("utf8");
    let buffer = "";
    socket.on("data", (chunk) => {
      buffer += chunk;
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
        if (line) onLine(socket, line, handle);
      }
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(socketPath, () => {
      server.removeListener("error", reject);
      resolve();
    });
  });
  return server;
}
