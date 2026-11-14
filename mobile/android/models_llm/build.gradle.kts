// On-demand Play Asset Delivery pack. CI copies the checked model files into src/main/assets
// (tool/fill_asset_packs.py); the folder is empty in git.
plugins {
    id("com.android.asset-pack")
}

assetPack {
    packName.set("models_llm")
    dynamicDelivery {
        deliveryType.set("on-demand")
    }
}
