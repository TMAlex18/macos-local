/* Minimal Vulkan compute -> host coherent memory readback.
 * Smoke test ONLY; no latency/throughput claim from one dispatch.
 */
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CHECK(expr) do { VkResult _r=(expr); if (_r != VK_SUCCESS) { \
 fprintf(stderr, "Vulkan error at line %d: %s returned %d\n", __LINE__, #expr, _r); \
 return 3; } } while (0)
static double monotonic_ms(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return 1000.0 * ts.tv_sec + 0.000001 * ts.tv_nsec;
}
int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s {intel|nvidia} compute.spv\n", argv[0]); return 2;
    }
    uint32_t vendor = strcmp(argv[1],"intel")==0 ? 0x8086 :
                      strcmp(argv[1],"nvidia")==0 ? 0x10de : 0;
    if (!vendor) { fprintf(stderr,"Unknown device vendor\n"); return 2; }

    VkApplicationInfo ai = { .sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName="macos-local-e02", .apiVersion=VK_API_VERSION_1_0 };
    VkInstanceCreateInfo ici = { .sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo=&ai };
    VkInstance inst=VK_NULL_HANDLE;
    CHECK(vkCreateInstance(&ici,NULL,&inst));
    uint32_t n=0; CHECK(vkEnumeratePhysicalDevices(inst,&n,NULL));
    if (n==0 || n>32) { fprintf(stderr,"Bad physical-device count %u\n",n); return 3; }
    VkPhysicalDevice pd[32]; CHECK(vkEnumeratePhysicalDevices(inst,&n,pd));
    VkPhysicalDevice selected=VK_NULL_HANDLE;
    VkPhysicalDeviceProperties props;
    for (uint32_t i=0;i<n;i++) {
        vkGetPhysicalDeviceProperties(pd[i],&props);
        if (props.vendorID==vendor &&
            ((vendor==0x8086 && props.deviceType==VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU) ||
             (vendor==0x10de && props.deviceType==VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU))) {
            selected=pd[i]; break;
        }
    }
    if (!selected) { fprintf(stderr,"Requested GPU not enumerated\n"); return 4; }
    vkGetPhysicalDeviceProperties(selected,&props);
    uint32_t qn=0; vkGetPhysicalDeviceQueueFamilyProperties(selected,&qn,NULL);
    if (qn==0 || qn>64) return 5;
    VkQueueFamilyProperties qfs[64]; vkGetPhysicalDeviceQueueFamilyProperties(selected,&qn,qfs);
    uint32_t qfam=qn;
    for(uint32_t i=0;i<qn;i++) if ((qfs[i].queueFlags & VK_QUEUE_COMPUTE_BIT) && qfs[i].queueCount) {
        qfam=i;break;
    }
    if (qfam==qn) { fprintf(stderr,"No compute queue\n"); return 6; }
    float prio=1.0f;
    VkDeviceQueueCreateInfo qci={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex=qfam,.queueCount=1,.pQueuePriorities=&prio};
    VkDeviceCreateInfo dci={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount=1,.pQueueCreateInfos=&qci};
    VkDevice dev=VK_NULL_HANDLE; CHECK(vkCreateDevice(selected,&dci,NULL,&dev));
    VkQueue queue;vkGetDeviceQueue(dev,qfam,0,&queue);

    VkBufferCreateInfo bci={.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size=1024,.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
        .sharingMode=VK_SHARING_MODE_EXCLUSIVE};
    VkBuffer buf; CHECK(vkCreateBuffer(dev,&bci,NULL,&buf));
    VkMemoryRequirements req; vkGetBufferMemoryRequirements(dev,buf,&req);
    VkPhysicalDeviceMemoryProperties memprops;
    vkGetPhysicalDeviceMemoryProperties(selected,&memprops);
    int chosen=-1;
    for(uint32_t i=0;i<memprops.memoryTypeCount;i++) {
        if ((req.memoryTypeBits & (1u<<i)) &&
          (memprops.memoryTypes[i].propertyFlags &
           (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) ==
           (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
            chosen=(int)i; break;
        }
    }
    if(chosen<0){fprintf(stderr,"No host-visible coherent buffer memory\n");return 7;}
    VkMemoryAllocateInfo mai={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize=req.size,.memoryTypeIndex=(uint32_t)chosen};
    VkDeviceMemory memory;CHECK(vkAllocateMemory(dev,&mai,NULL,&memory));
    CHECK(vkBindBufferMemory(dev,buf,memory,0));

    VkDescriptorSetLayoutBinding bind={.binding=0,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
        .descriptorCount=1,.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT};
    VkDescriptorSetLayoutCreateInfo slci={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount=1,.pBindings=&bind};
    VkDescriptorSetLayout sl;CHECK(vkCreateDescriptorSetLayout(dev,&slci,NULL,&sl));
    VkPipelineLayoutCreateInfo plci={.sType=VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount=1,.pSetLayouts=&sl};
    VkPipelineLayout pl;CHECK(vkCreatePipelineLayout(dev,&plci,NULL,&pl));
    VkDescriptorPoolSize ps={.type=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.descriptorCount=1};
    VkDescriptorPoolCreateInfo pci={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets=1,.poolSizeCount=1,.pPoolSizes=&ps};
    VkDescriptorPool pool;CHECK(vkCreateDescriptorPool(dev,&pci,NULL,&pool));
    VkDescriptorSetAllocateInfo sai={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool=pool,.descriptorSetCount=1,.pSetLayouts=&sl};
    VkDescriptorSet set;CHECK(vkAllocateDescriptorSets(dev,&sai,&set));
    VkDescriptorBufferInfo db={.buffer=buf,.offset=0,.range=1024};
    VkWriteDescriptorSet wr={.sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
        .dstSet=set,.dstBinding=0,.descriptorCount=1,
        .descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.pBufferInfo=&db};
    vkUpdateDescriptorSets(dev,1,&wr,0,NULL);

    FILE *f=fopen(argv[2],"rb");if(!f){perror("SPIR-V file");return 8;}
    if(fseek(f,0,SEEK_END)){perror("seek");return 8;}
    long len=ftell(f);rewind(f);
    if(len<=0 || len%4 || len>1024*1024){fprintf(stderr,"Invalid SPIR-V size\n");return 8;}
    uint32_t *words=malloc((size_t)len);if(!words){return 9;}
    if(fread(words,1,(size_t)len,f)!=(size_t)len){perror("read");return 8;}fclose(f);
    VkShaderModuleCreateInfo sci={.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize=(size_t)len,.pCode=words};
    VkShaderModule shader;CHECK(vkCreateShaderModule(dev,&sci,NULL,&shader));free(words);
    VkComputePipelineCreateInfo cpci={.sType=VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage={.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
                .stage=VK_SHADER_STAGE_COMPUTE_BIT,.module=shader,.pName="main"},
        .layout=pl};
    VkPipeline pipeline;CHECK(vkCreateComputePipelines(dev,VK_NULL_HANDLE,1,&cpci,NULL,&pipeline));
    VkCommandPoolCreateInfo cp={.sType=VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex=qfam};
    VkCommandPool cmdpool;CHECK(vkCreateCommandPool(dev,&cp,NULL,&cmdpool));
    VkCommandBufferAllocateInfo cba={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool=cmdpool,.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY,.commandBufferCount=1};
    VkCommandBuffer command;CHECK(vkAllocateCommandBuffers(dev,&cba,&command));
    VkCommandBufferBeginInfo cbbi={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    CHECK(vkBeginCommandBuffer(command,&cbbi));
    vkCmdBindPipeline(command,VK_PIPELINE_BIND_POINT_COMPUTE,pipeline);
    vkCmdBindDescriptorSets(command,VK_PIPELINE_BIND_POINT_COMPUTE,pl,0,1,&set,0,NULL);
    vkCmdDispatch(command,4,1,1);
    CHECK(vkEndCommandBuffer(command));
    VkSubmitInfo submit={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,
        .pCommandBuffers=&command};
    double t0=monotonic_ms();
    CHECK(vkQueueSubmit(queue,1,&submit,VK_NULL_HANDLE));
    CHECK(vkQueueWaitIdle(queue));
    double elapsed=monotonic_ms()-t0;
    void *mapped=NULL;CHECK(vkMapMemory(dev,memory,0,1024,0,&mapped));
    uint32_t *result=(uint32_t*)mapped;
    uint64_t sum=0;uint32_t errors=0;
    for(uint32_t i=0;i<256;i++){
        uint32_t expected=(i*1664525u)^1013904223u;
        if(result[i]!=expected){if(errors<4)fprintf(stderr,"Mismatch at %u: %u != %u\n",i,result[i],expected);errors++;}
        sum+=(uint64_t)result[i];
    }
    vkUnmapMemory(dev,memory);
    printf("GPU=%s vendor=%04x device=%04x api=%u.%u queue_family=%u\n",
        props.deviceName,props.vendorID,props.deviceID,
        VK_VERSION_MAJOR(props.apiVersion),VK_VERSION_MINOR(props.apiVersion),qfam);
    printf("compute_words=256 mismatches=%u checksum=%llu submit_wait_ms=%.3f (single-sample, not a benchmark)\n",
        errors,(unsigned long long)sum,elapsed);
    vkDestroyCommandPool(dev,cmdpool,NULL);
    vkDestroyPipeline(dev,pipeline,NULL);
    vkDestroyShaderModule(dev,shader,NULL);
    vkDestroyDescriptorPool(dev,pool,NULL);
    vkDestroyPipelineLayout(dev,pl,NULL);
    vkDestroyDescriptorSetLayout(dev,sl,NULL);
    vkDestroyBuffer(dev,buf,NULL);
    vkFreeMemory(dev,memory,NULL);
    vkDestroyDevice(dev,NULL);
    vkDestroyInstance(inst,NULL);
    return errors ? 11 : 0;
}
