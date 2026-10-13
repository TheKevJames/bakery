/**
 * ketch's external PDF converter (see ../ketch.ts): prints the text of the
 * PDF at argv[2]. ketch's built-in parser loses the spaces in LaTeX output
 * (eg. arXiv papers); pdf.js, through unpdf, keeps them.
 */
import { readFile } from "node:fs/promises";
import { extractText, getDocumentProxy } from "unpdf";

const pdf = await getDocumentProxy(new Uint8Array(await readFile(process.argv[2])));
const { text } = await extractText(pdf, { mergePages: true });
process.stdout.write(text);
