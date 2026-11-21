import express from "express";

import { register, login } from "../controller/user.controller.js";

import { validate } from "../../../middleware/validation.middleware.js";

import { registerSchema, loginSchema } from "../validation/user.validation.js";

const router = express.Router();

router.post("/register", validate(registerSchema), register);

router.post("/login", validate(loginSchema), login);

export default router;
