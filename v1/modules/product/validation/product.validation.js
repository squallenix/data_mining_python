import Joi from "joi";

const imageSchema = Joi.object({
  url: Joi.string().uri().required(),
  alt: Joi.string().allow("").default(""),
  isPrimary: Joi.boolean().default(false),
  sortOrder: Joi.number().integer().min(0).default(0),
});

const variantSchema = Joi.object({
  sku: Joi.string().allow(""),
  name: Joi.string().allow(""),

  attributes: Joi.object().default({}),

  price: Joi.number().min(0).required(),

  compareAtPrice: Joi.number().min(0).allow(null),

  costPrice: Joi.number().min(0).allow(null),

  stock: Joi.number().integer().min(0).default(0),

  lowStockThreshold: Joi.number().integer().min(0).default(5),

  images: Joi.array()
    .items(imageSchema)
    .default([]),

  isActive: Joi.boolean().default(true),
});

const sourceSchema = Joi.object({
  name: Joi.string().required(),

  externalId: Joi.string().allow(""),

  url: Joi.string().uri().allow(""),

  lastSyncedAt: Joi.date().allow(null),
});

export const createProductSchema = Joi.object({
  name: Joi.string().trim().required(),

  slug: Joi.string().trim().required(),

  description: Joi.string().allow("").default(""),

  shortDescription: Joi.string().allow("").default(""),

  brand: Joi.string().allow(null),

  categories: Joi.array()
    .items(Joi.string())
    .default([]),

  sku: Joi.string().allow(""),

  barcode: Joi.string().allow(""),

  mpn: Joi.string().allow(""),

  gtin: Joi.string().allow(""),

  price: Joi.number().min(0).required(),

  compareAtPrice: Joi.number()
    .min(0)
    .allow(null),

  currency: Joi.string()
    .default("BDT"),

  stock: Joi.number()
    .integer()
    .min(0)
    .default(0),

  lowStockThreshold: Joi.number()
    .integer()
    .min(0)
    .default(5),

  trackInventory: Joi.boolean()
    .default(true),

  allowBackorder: Joi.boolean()
    .default(false),

  images: Joi.array()
    .items(imageSchema)
    .default([]),

  specifications: Joi.object()
    .default({}),

  attributes: Joi.object()
    .default({}),

  hasVariants: Joi.boolean()
    .default(false),

  variants: Joi.array()
    .items(variantSchema)
    .default([]),

  weight: Joi.object({
    value: Joi.number().min(0).allow(null),
    unit: Joi.string()
      .valid("g", "kg", "oz", "lb")
      .default("kg"),
  }).allow(null),

  dimensions: Joi.object({
    length: Joi.number().min(0).allow(null),
    width: Joi.number().min(0).allow(null),
    height: Joi.number().min(0).allow(null),
    unit: Joi.string()
      .valid("cm", "m", "in")
      .default("cm"),
  }).allow(null),

  requiresShipping: Joi.boolean()
    .default(true),

  shippingClass: Joi.string()
    .allow(null, ""),

  status: Joi.string()
    .valid("draft", "active", "archived")
    .default("draft"),

  isVisible: Joi.boolean()
    .default(true),

  seo: Joi.object({
    title: Joi.string().allow(""),
    description: Joi.string().allow(""),
    keywords: Joi.array()
      .items(Joi.string())
      .default([]),
  }),

  rating: Joi.object({
    average: Joi.number()
      .min(0)
      .max(5)
      .default(0),

    count: Joi.number()
      .integer()
      .min(0)
      .default(0),
  }),

  sources: Joi.array()
    .items(sourceSchema)
    .default([]),
});

export const updateProductSchema = createProductSchema.fork(
  ["name", "slug", "price"],
  (schema) => schema.optional(),
);