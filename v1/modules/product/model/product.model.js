import mongoose from "mongoose";

const imageSchema = new mongoose.Schema(
  {
    url: {
      type: String,
      required: true,
    },

    alt: {
      type: String,
      default: "",
    },

    isPrimary: {
      type: Boolean,
      default: false,
    },

    sortOrder: {
      type: Number,
      default: 0,
    },
  },
  { _id: false },
);

const variantSchema = new mongoose.Schema(
  {
    sku: {
      type: String,
    },

    name: {
      type: String,
    },

    attributes: {
      type: Map,
      of: String,
      default: {},
    },

    price: {
      type: Number,
      required: true,
    },

    compareAtPrice: {
      type: Number,
      default: null,
    },

    costPrice: {
      type: Number,
      default: null,
    },

    stock: {
      type: Number,
      default: 0,
    },

    lowStockThreshold: {
      type: Number,
      default: 5,
    },

    images: {
      type: [imageSchema],
      default: [],
    },

    isActive: {
      type: Boolean,
      default: true,
    },
  },
  {
    timestamps: true,
    _id: false,
  },
);

const sourceSchema = new mongoose.Schema(
  {
    name: {
      type: String,
      required: true,
    },

    externalId: {
      type: String,
    },

    url: {
      type: String,
    },

    lastSyncedAt: {
      type: Date,
      default: null,
    },
  },
  { _id: false },
);

const productSchema = new mongoose.Schema(
  {
    name: {
      type: String,
      required: true,
      trim: true,
    },

    slug: {
      type: String,
      required: true,
      unique: true,
      trim: true,
    },

    description: {
      type: String,
      default: "",
    },

    shortDescription: {
      type: String,
      default: "",
    },

    brand: {
      type: mongoose.Schema.Types.ObjectId,
      ref: "Brand",
      default: null,
    },

    categories: [
      {
        type: mongoose.Schema.Types.ObjectId,
        ref: "Category",
      },
    ],

    sku: {
      type: String,
    },

    barcode: {
      type: String,
    },

    mpn: {
      type: String,
    },

    gtin: {
      type: String,
    },

    price: {
      type: Number,
      required: true,
    },

    compareAtPrice: {
      type: Number,
      default: null,
    },

    currency: {
      type: String,
      required: true,
      default: "BDT",
    },

    stock: {
      type: Number,
      default: 0,
    },

    lowStockThreshold: {
      type: Number,
      default: 5,
    },

    trackInventory: {
      type: Boolean,
      default: true,
    },

    allowBackorder: {
      type: Boolean,
      default: false,
    },

    images: {
      type: [imageSchema],
      default: [],
    },

    specifications: {
      type: Map,
      of: mongoose.Schema.Types.Mixed,
      default: {},
    },

    attributes: {
      type: Map,
      of: mongoose.Schema.Types.Mixed,
      default: {},
    },

    hasVariants: {
      type: Boolean,
      default: false,
    },

    variants: {
      type: [variantSchema],
      default: [],
    },

    weight: {
      value: {
        type: Number,
        default: null,
      },

      unit: {
        type: String,
        enum: ["g", "kg", "oz", "lb"],
        default: "kg",
      },
    },

    dimensions: {
      length: {
        type: Number,
        default: null,
      },

      width: {
        type: Number,
        default: null,
      },

      height: {
        type: Number,
        default: null,
      },

      unit: {
        type: String,
        enum: ["cm", "m", "in"],
        default: "cm",
      },
    },

    requiresShipping: {
      type: Boolean,
      default: true,
    },

    shippingClass: {
      type: String,
      default: null,
    },

    status: {
      type: String,
      enum: ["draft", "active", "archived"],
      default: "draft",
    },

    isVisible: {
      type: Boolean,
      default: true,
    },

    seo: {
      title: {
        type: String,
      },

      description: {
        type: String,
      },

      keywords: {
        type: [String],
        default: [],
      },
    },

    rating: {
      average: {
        type: Number,
        default: 0,
      },

      count: {
        type: Number,
        default: 0,
      },
    },

    sources: {
      type: [sourceSchema],
      default: [],
    },
  },
  {
    timestamps: true,
  },
);

const Product = mongoose.model("Product", productSchema);

export default Product;