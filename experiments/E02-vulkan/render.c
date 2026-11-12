/* Offscreen 64x64 triangle with readback: validates raster/pipeline and copy.
 * Smoke test on the selected physical device; no presentation or cross-GPU.
 */
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#define N 64
#define VKOK(x) do { VkResult status__=(x); if(status__!=VK_SUCCESS) { \
  fprintf(stderr,"VK failure %s line=%d code=%d\n",#x,__LINE__,status__);return 8; } } while(0)

static unsigned char *load(const char *name, size_t *sz) {
    FILE *f=fopen(name,"rb"); if(!f){perror(name);return NULL;}
    fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);
    if(n<=0 || n>1048576 || n%4){fclose(f);return NULL;}
    unsigned char *buf=malloc((size_t)n);
    if(!buf || fread(buf,1,(size_t)n,f)!=(size_t)n){fclose(f);free(buf);return NULL;}
    fclose(f);*sz=(size_t)n;return buf;
}
static uint32_t memory_index(const VkPhysicalDeviceMemoryProperties *m,
                             uint32_t allowed, VkMemoryPropertyFlags wanted) {
    for(uint32_t i=0;i<m->memoryTypeCount;i++)
       if((allowed & (1u<<i)) &&
           (m->memoryTypes[i].propertyFlags & wanted)==wanted) return i;
    return UINT32_MAX;
}
int main(int argc,char**argv){
    if(argc!=4){fprintf(stderr,"Usage: %s intel|nvidia vert.spv frag.spv\n",argv[0]);return 2;}
    uint32_t vendor=strcmp(argv[1],"intel")==0?0x8086:
                    strcmp(argv[1],"nvidia")==0?0x10de:0;
    if(!vendor) return 2;
    VkApplicationInfo ai={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .apiVersion=VK_API_VERSION_1_0,.pApplicationName="macos-research-e02-render"};
    VkInstanceCreateInfo ici={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo=&ai};
    VkInstance instance; VKOK(vkCreateInstance(&ici,NULL,&instance));
    uint32_t n=0; VKOK(vkEnumeratePhysicalDevices(instance,&n,NULL));
    if(n==0||n>32){fprintf(stderr,"physical device count=%u\n",n);return 3;}
    VkPhysicalDevice devices[32],pd=VK_NULL_HANDLE;
    VKOK(vkEnumeratePhysicalDevices(instance,&n,devices));
    VkPhysicalDeviceProperties props;
    for(uint32_t i=0;i<n;i++){
        vkGetPhysicalDeviceProperties(devices[i],&props);
        if(props.vendorID==vendor && ((vendor==0x8086 && props.deviceType==1) ||
                (vendor==0x10de && props.deviceType==2))){pd=devices[i];break;}
    }
    if(!pd){fprintf(stderr,"Requested GPU not found\n");return 3;}
    vkGetPhysicalDeviceProperties(pd,&props);
    uint32_t qn=0; vkGetPhysicalDeviceQueueFamilyProperties(pd,&qn,NULL);
    if(!qn||qn>64)return 3;
    VkQueueFamilyProperties qfs[64];vkGetPhysicalDeviceQueueFamilyProperties(pd,&qn,qfs);
    uint32_t qfam=qn;
    for(uint32_t i=0;i<qn;i++)
       if(qfs[i].queueFlags&VK_QUEUE_GRAPHICS_BIT){qfam=i;break;}
    if(qfam==qn){fprintf(stderr,"graphics queue not found\n");return 3;}
    float priority=1.0f;
    VkDeviceQueueCreateInfo qci={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex=qfam,.queueCount=1,.pQueuePriorities=&priority};
    VkDeviceCreateInfo dci={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount=1,.pQueueCreateInfos=&qci};
    VkDevice dev;VKOK(vkCreateDevice(pd,&dci,NULL,&dev));
    VkQueue queue;vkGetDeviceQueue(dev,qfam,0,&queue);
    VkPhysicalDeviceMemoryProperties mem;
    vkGetPhysicalDeviceMemoryProperties(pd,&mem);
    VkImageCreateInfo ici2={.sType=VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType=VK_IMAGE_TYPE_2D,.format=VK_FORMAT_R8G8B8A8_UNORM,
        .extent={N,N,1},.mipLevels=1,.arrayLayers=1,.samples=VK_SAMPLE_COUNT_1_BIT,
        .tiling=VK_IMAGE_TILING_OPTIMAL,
        .usage=VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT|VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
        .sharingMode=VK_SHARING_MODE_EXCLUSIVE,.initialLayout=VK_IMAGE_LAYOUT_UNDEFINED};
    VkImage img;VKOK(vkCreateImage(dev,&ici2,NULL,&img));
    VkMemoryRequirements req;vkGetImageMemoryRequirements(dev,img,&req);
    uint32_t type=memory_index(&mem,req.memoryTypeBits,VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if(type==UINT32_MAX)type=memory_index(&mem,req.memoryTypeBits,0);
    if(type==UINT32_MAX)return 4;
    VkMemoryAllocateInfo mai={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize=req.size,.memoryTypeIndex=type};
    VkDeviceMemory imgmem;VKOK(vkAllocateMemory(dev,&mai,NULL,&imgmem));
    VKOK(vkBindImageMemory(dev,img,imgmem,0));
    VkImageViewCreateInfo vi={.sType=VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
        .image=img,.viewType=VK_IMAGE_VIEW_TYPE_2D,.format=ici2.format,
        .subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1}};
    VkImageView view;VKOK(vkCreateImageView(dev,&vi,NULL,&view));
    VkBufferCreateInfo bi={.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size=N*N*4,.usage=VK_BUFFER_USAGE_TRANSFER_DST_BIT,
        .sharingMode=VK_SHARING_MODE_EXCLUSIVE};
    VkBuffer buf;VKOK(vkCreateBuffer(dev,&bi,NULL,&buf));
    vkGetBufferMemoryRequirements(dev,buf,&req);
    type=memory_index(&mem,req.memoryTypeBits,
            VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if(type==UINT32_MAX){fprintf(stderr,"No coherent host staging memory\n");return 4;}
    mai.allocationSize=req.size;mai.memoryTypeIndex=type;
    VkDeviceMemory bufmem;VKOK(vkAllocateMemory(dev,&mai,NULL,&bufmem));
    VKOK(vkBindBufferMemory(dev,buf,bufmem,0));

    VkAttachmentDescription color={.format=ici2.format,.samples=VK_SAMPLE_COUNT_1_BIT,
        .loadOp=VK_ATTACHMENT_LOAD_OP_CLEAR,.storeOp=VK_ATTACHMENT_STORE_OP_STORE,
        .stencilLoadOp=VK_ATTACHMENT_LOAD_OP_DONT_CARE,
        .stencilStoreOp=VK_ATTACHMENT_STORE_OP_DONT_CARE,
        .initialLayout=VK_IMAGE_LAYOUT_UNDEFINED,
        .finalLayout=VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL};
    VkAttachmentReference ar={.attachment=0,.layout=VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
    VkSubpassDescription sp={.pipelineBindPoint=VK_PIPELINE_BIND_POINT_GRAPHICS,
        .colorAttachmentCount=1,.pColorAttachments=&ar};
    VkSubpassDependency dep={.srcSubpass=0,.dstSubpass=VK_SUBPASS_EXTERNAL,
        .srcStageMask=VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
        .dstStageMask=VK_PIPELINE_STAGE_TRANSFER_BIT,
        .srcAccessMask=VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
        .dstAccessMask=VK_ACCESS_TRANSFER_READ_BIT};
    VkRenderPassCreateInfo rpci={.sType=VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO,
        .attachmentCount=1,.pAttachments=&color,.subpassCount=1,.pSubpasses=&sp,
        .dependencyCount=1,.pDependencies=&dep};
    VkRenderPass rp;VKOK(vkCreateRenderPass(dev,&rpci,NULL,&rp));
    VkFramebufferCreateInfo fci={.sType=VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO,
        .renderPass=rp,.attachmentCount=1,.pAttachments=&view,
        .width=N,.height=N,.layers=1};
    VkFramebuffer fb;VKOK(vkCreateFramebuffer(dev,&fci,NULL,&fb));
    size_t len1=0,len2=0;unsigned char *vwords=load(argv[2],&len1);
    unsigned char *fwords=load(argv[3],&len2);
    if(!vwords||!fwords){fprintf(stderr,"Unable to load SPIR-V\n");return 5;}
    VkShaderModuleCreateInfo smci={.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    VkShaderModule vert,frag;
    smci.codeSize=len1;smci.pCode=(const uint32_t*)vwords;
    VKOK(vkCreateShaderModule(dev,&smci,NULL,&vert));
    smci.codeSize=len2;smci.pCode=(const uint32_t*)fwords;
    VKOK(vkCreateShaderModule(dev,&smci,NULL,&frag));
    free(vwords);free(fwords);
    VkPipelineLayoutCreateInfo plci={.sType=VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    VkPipelineLayout pl;VKOK(vkCreatePipelineLayout(dev,&plci,NULL,&pl));
    VkPipelineShaderStageCreateInfo stages[2]={
       {.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
        .stage=VK_SHADER_STAGE_VERTEX_BIT,.module=vert,.pName="main"},
       {.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
        .stage=VK_SHADER_STAGE_FRAGMENT_BIT,.module=frag,.pName="main"}};
    VkPipelineVertexInputStateCreateInfo vertex={.sType=VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
    VkPipelineInputAssemblyStateCreateInfo assembly={.sType=VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
        .topology=VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST};
    VkViewport viewport={0,0,N,N,0,1};VkRect2D scissor={{0,0},{N,N}};
    VkPipelineViewportStateCreateInfo vp={.sType=VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
        .viewportCount=1,.pViewports=&viewport,.scissorCount=1,.pScissors=&scissor};
    VkPipelineRasterizationStateCreateInfo rs={.sType=VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
        .polygonMode=VK_POLYGON_MODE_FILL,.cullMode=VK_CULL_MODE_NONE,
        .frontFace=VK_FRONT_FACE_COUNTER_CLOCKWISE,.lineWidth=1.0f};
    VkPipelineMultisampleStateCreateInfo ms={.sType=VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
        .rasterizationSamples=VK_SAMPLE_COUNT_1_BIT};
    VkPipelineColorBlendAttachmentState ca={.colorWriteMask=VK_COLOR_COMPONENT_R_BIT|
      VK_COLOR_COMPONENT_G_BIT|VK_COLOR_COMPONENT_B_BIT|VK_COLOR_COMPONENT_A_BIT};
    VkPipelineColorBlendStateCreateInfo blend={.sType=VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
        .attachmentCount=1,.pAttachments=&ca};
    VkGraphicsPipelineCreateInfo gpci={.sType=VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,
        .stageCount=2,.pStages=stages,.pVertexInputState=&vertex,
        .pInputAssemblyState=&assembly,.pViewportState=&vp,.pRasterizationState=&rs,
        .pMultisampleState=&ms,.pColorBlendState=&blend,.layout=pl,
        .renderPass=rp,.subpass=0};
    VkPipeline pipeline;VKOK(vkCreateGraphicsPipelines(dev,VK_NULL_HANDLE,1,&gpci,NULL,&pipeline));
    VkCommandPoolCreateInfo cpci={.sType=VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex=qfam};
    VkCommandPool pool;VKOK(vkCreateCommandPool(dev,&cpci,NULL,&pool));
    VkCommandBufferAllocateInfo cbai={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool=pool,.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY,.commandBufferCount=1};
    VkCommandBuffer cb;VKOK(vkAllocateCommandBuffers(dev,&cbai,&cb));
    VkCommandBufferBeginInfo cbi={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    VKOK(vkBeginCommandBuffer(cb,&cbi));
    VkClearValue clear={.color={{0,0,0,1}}};
    VkRenderPassBeginInfo rpbi={.sType=VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO,
        .renderPass=rp,.framebuffer=fb,.renderArea={{0,0},{N,N}},
        .clearValueCount=1,.pClearValues=&clear};
    vkCmdBeginRenderPass(cb,&rpbi,VK_SUBPASS_CONTENTS_INLINE);
    vkCmdBindPipeline(cb,VK_PIPELINE_BIND_POINT_GRAPHICS,pipeline);
    vkCmdDraw(cb,3,1,0,0);
    vkCmdEndRenderPass(cb);
    VkBufferImageCopy copy={.bufferOffset=0,
        .imageSubresource={.aspectMask=VK_IMAGE_ASPECT_COLOR_BIT,.mipLevel=0,
                           .baseArrayLayer=0,.layerCount=1},
        .imageExtent={N,N,1}};
    vkCmdCopyImageToBuffer(cb,img,VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,buf,1,&copy);
    VkBufferMemoryBarrier barrier={.sType=VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT,.dstAccessMask=VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.buffer=buf,.offset=0,.size=VK_WHOLE_SIZE};
    vkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,
                         0,0,NULL,1,&barrier,0,NULL);
    VKOK(vkEndCommandBuffer(cb));
    VkSubmitInfo si={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&cb};
    VKOK(vkQueueSubmit(queue,1,&si,VK_NULL_HANDLE));
    VKOK(vkQueueWaitIdle(queue));
    void *mapped=NULL;VKOK(vkMapMemory(dev,bufmem,0,N*N*4,0,&mapped));
    uint8_t *pix=(uint8_t*)mapped;
    size_t middle=(32*N+32)*4,corner=(1*N+1)*4;
    printf("device=%s center_rgba=(%u,%u,%u,%u) corner_rgba=(%u,%u,%u,%u)\n",props.deviceName,
      pix[middle],pix[middle+1],pix[middle+2],pix[middle+3],
      pix[corner],pix[corner+1],pix[corner+2],pix[corner+3]);
    uint32_t errs=0;
    int expect[4]={51,153,230,255};
    for(int k=0;k<4;k++) if(abs((int)pix[middle+k]-expect[k])>2)errs++;
    for(int k=0;k<3;k++) if(pix[corner+k]!=0)errs++;
    printf("render_and_readback=%s pixel_mismatches=%u\n",errs?"FAIL":"PASS",errs);
    vkUnmapMemory(dev,bufmem);
    vkDestroyCommandPool(dev,pool,NULL);
    vkDestroyPipeline(dev,pipeline,NULL);
    vkDestroyPipelineLayout(dev,pl,NULL);
    vkDestroyShaderModule(dev,vert,NULL);vkDestroyShaderModule(dev,frag,NULL);
    vkDestroyFramebuffer(dev,fb,NULL);vkDestroyRenderPass(dev,rp,NULL);
    vkDestroyImageView(dev,view,NULL);vkDestroyImage(dev,img,NULL);
    vkFreeMemory(dev,imgmem,NULL);vkDestroyBuffer(dev,buf,NULL);
    vkFreeMemory(dev,bufmem,NULL);vkDestroyDevice(dev,NULL);vkDestroyInstance(instance,NULL);
    return errs?11:0;
}
