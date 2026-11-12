/* External-memory capability query. This DOES NOT validate cross-GPU import.
 * Tested host: Intel ANV and NVIDIA proprietary Vulkan ICDs.
 */
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdint.h>
int main(void) {
    VkApplicationInfo app={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .apiVersion=VK_API_VERSION_1_1,.pApplicationName="macos-local-E03"};
    VkInstanceCreateInfo ci={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo=&app};
    VkInstance instance=VK_NULL_HANDLE;
    VkResult r=vkCreateInstance(&ci,0,&instance);
    if(r!=VK_SUCCESS){fprintf(stderr,"vkCreateInstance failed: %d\n",r);return 1;}
    uint32_t count=0;r=vkEnumeratePhysicalDevices(instance,&count,0);
    if(r!=VK_SUCCESS||count>32||count<2)return 2;
    VkPhysicalDevice pds[32];r=vkEnumeratePhysicalDevices(instance,&count,pds);
    if(r!=VK_SUCCESS)return 2;
    VkExternalMemoryHandleTypeFlagBits handles[]={
       VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT,
       VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT
    };
    const char* hnames[]={"OPAQUE_FD","DMA_BUF_EXT"};
    for(uint32_t i=0;i<count;i++) {
      VkPhysicalDeviceProperties pp;
      vkGetPhysicalDeviceProperties(pds[i],&pp);
      if(pp.vendorID!=0x8086 && pp.vendorID!=0x10de)continue;
      printf("DEVICE %s vendor=%04x\n",pp.deviceName,pp.vendorID);
      for(uint32_t k=0;k<2;k++){
         VkPhysicalDeviceExternalBufferInfo ebi={
            .sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_BUFFER_INFO,
            .usage=VK_BUFFER_USAGE_TRANSFER_SRC_BIT|VK_BUFFER_USAGE_TRANSFER_DST_BIT,
            .handleType=handles[k]};
         VkExternalBufferProperties ebp={
            .sType=VK_STRUCTURE_TYPE_EXTERNAL_BUFFER_PROPERTIES};
         vkGetPhysicalDeviceExternalBufferProperties(pds[i],&ebi,&ebp);
         VkExternalMemoryProperties p=ebp.externalMemoryProperties;
         printf("  buffer handle=%s export=%s import=%s dedicated_only=%s compatible=0x%x\n",
            hnames[k],(p.externalMemoryFeatures&VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT)?"yes":"no",
            (p.externalMemoryFeatures&VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT)?"yes":"no",
            (p.externalMemoryFeatures&VK_EXTERNAL_MEMORY_FEATURE_DEDICATED_ONLY_BIT)?"yes":"no",
            p.compatibleHandleTypes);
         VkPhysicalDeviceExternalImageFormatInfo eifi={
            .sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO,
            .handleType=handles[k]};
         VkPhysicalDeviceImageFormatInfo2 ifi={
            .sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
            .pNext=&eifi,.format=VK_FORMAT_R8G8B8A8_UNORM,
            .type=VK_IMAGE_TYPE_2D,.tiling=VK_IMAGE_TILING_OPTIMAL,
            .usage=VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT};
         VkExternalImageFormatProperties eifp={
            .sType=VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES};
         VkImageFormatProperties2 ifp={
            .sType=VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2,.pNext=&eifp};
         r=vkGetPhysicalDeviceImageFormatProperties2(pds[i],&ifi,&ifp);
         if(r!=VK_SUCCESS) printf("  image rgba8/optimal handle=%s unsupported VkResult=%d\n",hnames[k],r);
         else {
           VkExternalMemoryProperties ip=eifp.externalMemoryProperties;
           printf("  image rgba8/optimal handle=%s export=%s import=%s dedicated_only=%s\n",
              hnames[k],(ip.externalMemoryFeatures&VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT)?"yes":"no",
              (ip.externalMemoryFeatures&VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT)?"yes":"no",
              (ip.externalMemoryFeatures&VK_EXTERNAL_MEMORY_FEATURE_DEDICATED_ONLY_BIT)?"yes":"no");
         }
      }
    }
    vkDestroyInstance(instance,0);
    return 0;
}
