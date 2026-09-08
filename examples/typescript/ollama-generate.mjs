import { connect } from "../../typescript/dist/index.js";
const model = connect({ driver: "ollama", model: "qwen3:8b" });
console.log((await model.generate("Hello")).text);
