import Product from "../model/product.model.js";

export const createProduct = async (data) => {
  const product = await Product.create(data);

  return product;
};

export const getProducts = async () => {
  const products = await Product.find();

  return products;
};

export const getProductById = async (id) => {
  const product = await Product.findById(id);

  if (!product) {
    throw new Error("Product not found");
  }

  return product;
};

export const updateProduct = async (id, data) => {
  const product = await Product.findByIdAndUpdate(
    id,
    data,
    {
      new: true,
      runValidators: true,
    },
  );

  if (!product) {
    throw new Error("Product not found");
  }

  return product;
};

export const deleteProduct = async (id) => {
  const product = await Product.findByIdAndDelete(id);

  if (!product) {
    throw new Error("Product not found");
  }

  return product;
};