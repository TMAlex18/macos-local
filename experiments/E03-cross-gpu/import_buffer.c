/* Experimental DMA_BUF cross-ICD buffer import on Intel and NVIDIA.
 * Tests only host mappings and Vulkan handle exchange (NOT GPU synchronisation).
 */
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

typedef struct Gpu {
    VkPhysicalDevice physical;
    VkDevice device;
    VkPhysicalDeviceProperties info;
    VkPhysicalDeviceMemoryProperties mem;
    PFN_vkGetMemoryFdKHR get_fd;
    PFN_vkGetMemoryFdPropertiesKHR get_fd_properties;
} Gpu;

static uint32_t memtype(const Gpu *g, uint32_t allowed,
                         VkMemoryPropertyFlags required) {
    for (uint32_t i=0;i<g->mem.memoryTypeCount;i++)
        if ((allowed&(1u<<i)) && ((g->mem.memoryTypes[i].propertyFlags&required)==required))
            return i;
    return UINT32_MAX;
}
static VkResult new_buffer(Gpu *g, VkBuffer *b, VkMemoryRequirements *req) {
    VkExternalMemoryBufferCreateInfo ebi={
       .sType=VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO,
       .handleTypes=VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
    VkBufferCreateInfo bi={.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
      .pNext=&ebi,.size=4096,
      .usage=VK_BUFFER_USAGE_TRANSFER_SRC_BIT|VK_BUFFER_USAGE_TRANSFER_DST_BIT,
      .sharingMode=VK_SHARING_MODE_EXCLUSIVE};
    VkResult r=vkCreateBuffer(g->device,&bi,NULL,b);
    if(r==VK_SUCCESS)vkGetBufferMemoryRequirements(g->device,*b,req);
    return r;
}
static int init_gpu(Gpu *g) {
    const char *extensions[] = {
        VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME,
        VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME
    };
    uint32_t qn=0;
    vkGetPhysicalDeviceQueueFamilyProperties(g->physical,&qn,NULL);
    if(qn==0||qn>64)return 1;
    VkQueueFamilyProperties families[64];
    vkGetPhysicalDeviceQueueFamilyProperties(g->physical,&qn,families);
    uint32_t family=qn;
    for(uint32_t i=0;i<qn;i++) {
        if(families[i].queueCount && (families[i].queueFlags & VK_QUEUE_TRANSFER_BIT)){
            family=i;break;
        }
    }
    if(family==qn)return 1;
    float priority=1.0f;
    VkDeviceQueueCreateInfo queueci={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
      .queueFamilyIndex=family,.queueCount=1,.pQueuePriorities=&priority};
    VkDeviceCreateInfo di={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
      .queueCreateInfoCount=1,.pQueueCreateInfos=&queueci,
      .enabledExtensionCount=2,.ppEnabledExtensionNames=extensions};
    VkResult r=vkCreateDevice(g->physical,&di,NULL,&g->device);
    if(r!=VK_SUCCESS){fprintf(stderr,"device create failed %s: %d\n",g->info.deviceName,r);return 1;}
    vkGetPhysicalDeviceMemoryProperties(g->physical,&g->mem);
    g->get_fd=(PFN_vkGetMemoryFdKHR)vkGetDeviceProcAddr(g->device,"vkGetMemoryFdKHR");
    g->get_fd_properties=(PFN_vkGetMemoryFdPropertiesKHR)vkGetDeviceProcAddr(g->device,"vkGetMemoryFdPropertiesKHR");
    if(!g->get_fd||!g->get_fd_properties){fprintf(stderr,"External FD functions missing\n");return 1;}
    return 0;
}
static void run(Gpu *src, Gpu *dst) {
    VkBuffer sb=VK_NULL_HANDLE,db=VK_NULL_HANDLE;
    VkDeviceMemory sm=VK_NULL_HANDLE,dm=VK_NULL_HANDLE;
    VkMemoryRequirements sr,dr;
    int fd=-1;void *smap=NULL,*dmap=NULL;
    const VkMemoryPropertyFlags host=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|
        VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    printf("CASE %s -> %s (DMA_BUF, 4096-byte transfer buffer)\n",
      src->info.deviceName,dst->info.deviceName);
    VkResult r=new_buffer(src,&sb,&sr);
    if(r!=VK_SUCCESS){printf("  SOURCE_BUFFER_FAIL %d\n",r);goto out;}
    uint32_t ti=memtype(src,sr.memoryTypeBits,host);
    if(ti==UINT32_MAX){puts("  SOURCE_HAS_NO_EXPORTABLE_HOST_VISIBLE_TYPE (not proof of unsupported device-local sharing)");goto out;}
    VkExportMemoryAllocateInfo eai={.sType=VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO,
        .handleTypes=VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
    VkMemoryAllocateInfo mai={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
       .pNext=&eai,.allocationSize=sr.size,.memoryTypeIndex=ti};
    r=vkAllocateMemory(src->device,&mai,NULL,&sm);
    if(r!=VK_SUCCESS){printf("  SOURCE_ALLOC_FAIL %d\n",r);goto out;}
    r=vkBindBufferMemory(src->device,sb,sm,0);
    if(r!=VK_SUCCESS){printf("  SOURCE_BIND_FAIL %d\n",r);goto out;}
    r=vkMapMemory(src->device,sm,0,4096,0,&smap);
    if(r!=VK_SUCCESS){printf("  SOURCE_MAP_FAIL %d\n",r);goto out;}
    for(int i=0;i<4096;i++)((uint8_t*)smap)[i]=(uint8_t)((i*13+79)&255);
    /* CPU-only mapping verification: no GPU commands, external semaphore,
     * or implicit GPU-vs-host visibility implied by this result.
     */
    VkMemoryGetFdInfoKHR fi={.sType=VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR,
      .memory=sm,.handleType=VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
    r=src->get_fd(src->device,&fi,&fd);
    if(r!=VK_SUCCESS){printf("  FD_EXPORT_FAIL %d\n",r);goto out;}
    r=new_buffer(dst,&db,&dr);
    if(r!=VK_SUCCESS){printf("  TARGET_BUFFER_FAIL %d\n",r);goto out;}
    VkMemoryFdPropertiesKHR fp={.sType=VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR};
    r=dst->get_fd_properties(dst->device,
             VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,fd,&fp);
    if(r!=VK_SUCCESS){printf("  FD_QUERY_IMPORT_FAIL %d\n",r);goto out;}
    ti=memtype(dst,dr.memoryTypeBits&fp.memoryTypeBits,host);
    if(ti==UINT32_MAX){
      printf("  TARGET_HAS_NO_HOST_VISIBLE_IMPORT_TYPE fd_bits=0x%x buf_bits=0x%x\n",
          fp.memoryTypeBits,dr.memoryTypeBits);
      goto out;
    }
    if(dr.size>sr.size){
       printf("  ALLOCATION_SIZE_INCOMPATIBLE src=%llu dst=%llu\n",
          (unsigned long long)sr.size,(unsigned long long)dr.size);
       goto out;
    }
    VkImportMemoryFdInfoKHR imp={.sType=VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR,
      .handleType=VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,.fd=fd};
    mai.pNext=&imp;mai.allocationSize=sr.size;mai.memoryTypeIndex=ti;
    r=vkAllocateMemory(dst->device,&mai,NULL,&dm);
    if(r!=VK_SUCCESS){printf("  FD_IMPORT_ALLOC_FAIL %d\n",r);goto out;}
    fd=-1; /* Vulkan takes ownership of imported FD on successful allocation. */
    r=vkBindBufferMemory(dst->device,db,dm,0);
    if(r!=VK_SUCCESS){printf("  FD_IMPORT_BIND_FAIL %d\n",r);goto out;}
    r=vkMapMemory(dst->device,dm,0,4096,0,&dmap);
    if(r!=VK_SUCCESS){printf("  FD_IMPORT_MAP_FAIL %d\n",r);goto out;}
    int bad=0;
    for(int i=0;i<4096;i++){
       uint8_t expected=(uint8_t)((i*13+79)&255);
       if(((uint8_t*)dmap)[i]!=expected)bad++;
    }
    printf("  HOST_MAPPING_SHARED_DATA: %s mismatches=%d\n",bad?"FAIL":"PASS",bad);
out:
    if(dmap)vkUnmapMemory(dst->device,dm);
    if(smap)vkUnmapMemory(src->device,sm);
    if(dm)vkFreeMemory(dst->device,dm,NULL);
    if(db)vkDestroyBuffer(dst->device,db,NULL);
    if(fd>=0)close(fd);
    if(sm)vkFreeMemory(src->device,sm,NULL);
    if(sb)vkDestroyBuffer(src->device,sb,NULL);
}
int main(void) {
    setvbuf(stdout,NULL,_IONBF,0);
    VkApplicationInfo ai={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,
       .apiVersion=VK_API_VERSION_1_1};
    VkInstanceCreateInfo ci={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
       .pApplicationInfo=&ai};
    VkInstance instance=VK_NULL_HANDLE;
    VkResult r=vkCreateInstance(&ci,NULL,&instance);
    if(r!=VK_SUCCESS){fprintf(stderr,"VkInstance error %d\n",r);return 2;}
    uint32_t n=0;vkEnumeratePhysicalDevices(instance,&n,NULL);
    if(n==0||n>32)return 2;
    VkPhysicalDevice pds[32];vkEnumeratePhysicalDevices(instance,&n,pds);
    Gpu intel={0},nvidia={0};
    for(uint32_t i=0;i<n;i++){
       VkPhysicalDeviceProperties p;vkGetPhysicalDeviceProperties(pds[i],&p);
       if(p.vendorID==0x8086 && p.deviceType==VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU){
          intel.physical=pds[i];intel.info=p;
       }
       if(p.vendorID==0x10de && p.deviceType==VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU){
          nvidia.physical=pds[i];nvidia.info=p;
       }
    }
    if(!intel.physical||!nvidia.physical){puts("Missing physical Intel or NVIDIA");return 2;}
    int fail=init_gpu(&intel);
    if(!fail)fail=init_gpu(&nvidia);
    if(!fail){
       run(&nvidia,&intel);
       run(&intel,&nvidia);
    }
    if(nvidia.device)vkDestroyDevice(nvidia.device,NULL);
    if(intel.device)vkDestroyDevice(intel.device,NULL);
    vkDestroyInstance(instance,NULL);
    return fail?3:0;
}
