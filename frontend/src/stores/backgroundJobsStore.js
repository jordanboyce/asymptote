import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import axios from 'axios'

export const useBackgroundJobsStore = defineStore('backgroundJobs', () => {
  // State
  const uploadJobs = ref([]) // { jobId, collectionId, status, progress, currentFile, ... }
  const reindexJob = ref(null) // Only one reindex job at a time
  const pollingInterval = ref(null)
  const eventSources = ref({}) // SSE connections per job

  // Computed
  const hasActiveJobs = computed(() => {
    const activeUploads = uploadJobs.value.some(j => j.status === 'pending' || j.status === 'running')
    const activeReindex = reindexJob.value && (reindexJob.value.status === 'pending' || reindexJob.value.status === 'running')
    return activeUploads || activeReindex
  })

  const activeJobCount = computed(() => {
    let count = 0
    count += uploadJobs.value.filter(j => j.status === 'pending' || j.status === 'running').length
    if (reindexJob.value && (reindexJob.value.status === 'pending' || reindexJob.value.status === 'running')) {
      count += 1
    }
    return count
  })

  const allJobs = computed(() => {
    const jobs = []

    // Add upload/index jobs
    uploadJobs.value.forEach(job => {
      jobs.push({
        type: job.jobType || 'upload',  // Use actual job type from backend
        id: job.jobId,
        collectionId: job.collectionId,
        status: job.status,
        progress: job.progressPercent || 0,
        currentFile: job.currentFile,
        totalFiles: job.totalFiles,
        processedFiles: job.processedFiles,
        error: job.error,
        startedAt: job.startedAt,
        // v4.0: Granular progress
        phase: job.phase,
        phaseProgress: job.phaseProgress || 0,
        phaseDetail: job.phaseDetail,
        chunksProcessed: job.chunksProcessed || 0,
        chunksTotal: job.chunksTotal || 0,
      })
    })

    // Add reindex job
    if (reindexJob.value) {
      jobs.push({
        type: 'reindex',
        id: reindexJob.value.id,
        collectionId: reindexJob.value.collection_id,
        status: reindexJob.value.status,
        progress: reindexJob.value.total_documents > 0
          ? Math.round((reindexJob.value.processed_documents / reindexJob.value.total_documents) * 100)
          : 0,
        currentFile: reindexJob.value.current_file,
        totalFiles: reindexJob.value.total_documents,
        processedFiles: reindexJob.value.processed_documents,
        error: reindexJob.value.error,
        startedAt: reindexJob.value.started_at,
      })
    }

    return jobs
  })

  // Actions
  function addUploadJob(jobData) {
    // Remove any existing job with same ID
    uploadJobs.value = uploadJobs.value.filter(j => j.jobId !== jobData.job_id)

    uploadJobs.value.push({
      jobId: jobData.job_id,
      collectionId: jobData.collection_id,
      status: jobData.status,
      totalFiles: jobData.total_files,
      processedFiles: jobData.processed_files,
      currentFile: jobData.current_file,
      progressPercent: jobData.progress_percent,
      error: jobData.error,
      resultSummary: jobData.result_summary,
      startedAt: jobData.started_at,
      completedAt: jobData.completed_at,
      // v4.0: Granular progress
      phase: jobData.phase,
      phaseProgress: jobData.phase_progress,
      phaseDetail: jobData.phase_detail,
      chunksProcessed: jobData.chunks_processed,
      chunksTotal: jobData.chunks_total,
      // Job type: 'upload' or 'index'
      jobType: jobData.job_type || 'upload',
    })

    // Start SSE stream for this job (v4.0)
    startSSEStream(jobData.job_id)

    // Also start polling as a fallback (SSE may not connect immediately)
    startPolling()
  }

  function updateUploadJob(jobData) {
    const job = uploadJobs.value.find(j => j.jobId === jobData.job_id)
    if (job) {
      job.status = jobData.status
      job.processedFiles = jobData.processed_files
      job.currentFile = jobData.current_file
      job.progressPercent = jobData.progress_percent
      job.error = jobData.error
      job.resultSummary = jobData.result_summary
      job.completedAt = jobData.completed_at
      // v4.0: Granular progress
      if (jobData.phase !== undefined) job.phase = jobData.phase
      if (jobData.phase_progress !== undefined) job.phaseProgress = jobData.phase_progress
      if (jobData.phase_detail !== undefined) job.phaseDetail = jobData.phase_detail
      if (jobData.chunks_processed !== undefined) job.chunksProcessed = jobData.chunks_processed
      if (jobData.chunks_total !== undefined) job.chunksTotal = jobData.chunks_total
      // Job type
      if (jobData.job_type !== undefined) job.jobType = jobData.job_type
    }
  }

  function updateFromSSEEvent(jobId, eventData) {
    const job = uploadJobs.value.find(j => j.jobId === jobId)
    if (!job) return

    // Update based on event data
    if (eventData.phase) job.phase = eventData.phase
    if (eventData.phase_progress !== undefined) job.phaseProgress = eventData.phase_progress
    if (eventData.phase_detail) job.phaseDetail = eventData.phase_detail
    if (eventData.current_file) job.currentFile = eventData.current_file
    if (eventData.file_index !== undefined) job.processedFiles = eventData.file_index
    if (eventData.total_files !== undefined) job.totalFiles = eventData.total_files
    if (eventData.chunks_processed !== undefined) job.chunksProcessed = eventData.chunks_processed
    if (eventData.chunks_total !== undefined) job.chunksTotal = eventData.chunks_total
    if (eventData.overall_percent !== undefined) job.progressPercent = eventData.overall_percent
    if (eventData.error) job.error = eventData.error

    // Update status based on event type
    if (eventData.event_type === 'job_complete') {
      job.status = 'completed'
    } else if (eventData.event_type === 'job_error') {
      job.status = 'failed'
    } else if (eventData.event_type === 'job_cancelled') {
      job.status = 'cancelled'
    } else if (eventData.event_type === 'file_start' || eventData.event_type === 'phase_progress') {
      job.status = 'running'
    }
  }

  function startSSEStream(jobId) {
    // Don't create duplicate connections
    if (eventSources.value[jobId]) {
      return
    }

    const eventSource = new EventSource(`/documents/upload/${jobId}/stream`)

    eventSource.addEventListener('connected', (e) => {
      console.log(`SSE connected for job ${jobId}`)
      const data = JSON.parse(e.data)
      updateFromSSEEvent(jobId, data)
    })

    eventSource.addEventListener('file_start', (e) => {
      const data = JSON.parse(e.data)
      updateFromSSEEvent(jobId, data)
    })

    eventSource.addEventListener('phase_progress', (e) => {
      const data = JSON.parse(e.data)
      updateFromSSEEvent(jobId, data)
    })

    eventSource.addEventListener('file_complete', (e) => {
      const data = JSON.parse(e.data)
      updateFromSSEEvent(jobId, data)
    })

    eventSource.addEventListener('file_error', (e) => {
      const data = JSON.parse(e.data)
      updateFromSSEEvent(jobId, data)
    })

    eventSource.addEventListener('job_complete', (e) => {
      const data = JSON.parse(e.data)
      updateFromSSEEvent(jobId, data)
      closeSSEStream(jobId)
      stopPollingIfNoActiveJobs()
    })

    eventSource.addEventListener('job_error', (e) => {
      const data = JSON.parse(e.data)
      updateFromSSEEvent(jobId, data)
      closeSSEStream(jobId)
      stopPollingIfNoActiveJobs()
    })

    eventSource.addEventListener('job_cancelled', (e) => {
      const data = JSON.parse(e.data)
      updateFromSSEEvent(jobId, data)
      closeSSEStream(jobId)
      stopPollingIfNoActiveJobs()
    })

    eventSource.onerror = (err) => {
      console.warn(`SSE error for job ${jobId}, falling back to polling`, err)
      closeSSEStream(jobId)
      // Fall back to polling if SSE fails
      startPolling()
    }

    eventSources.value[jobId] = eventSource
  }

  function closeSSEStream(jobId) {
    if (eventSources.value[jobId]) {
      eventSources.value[jobId].close()
      delete eventSources.value[jobId]
    }
  }

  function closeAllSSEStreams() {
    Object.keys(eventSources.value).forEach(jobId => {
      closeSSEStream(jobId)
    })
  }

  function stopPollingIfNoActiveJobs() {
    if (!hasActiveJobs.value) {
      stopPolling()
    }
  }

  function removeUploadJob(jobId) {
    closeSSEStream(jobId)
    uploadJobs.value = uploadJobs.value.filter(j => j.jobId !== jobId)

    // Stop polling if no more active jobs
    if (!hasActiveJobs.value) {
      stopPolling()
    }
  }

  async function cancelUploadJob(jobId) {
    try {
      const response = await axios.post(`/documents/upload/${jobId}/cancel`)
      // If force-cancelled (status === 'cancelled'), update local state immediately
      // Otherwise SSE will handle the update
      if (response.data.status === 'cancelled') {
        const job = uploadJobs.value.find(j => j.jobId === jobId)
        if (job) {
          job.status = 'cancelled'
          job.error = response.data.message
        }
        closeSSEStream(jobId)
        stopPollingIfNoActiveJobs()
      }
    } catch (err) {
      console.error(`Failed to cancel job ${jobId}:`, err)
      throw err
    }
  }

  function setReindexJob(jobData) {
    reindexJob.value = jobData
    if (jobData && (jobData.status === 'pending' || jobData.status === 'running')) {
      startPolling()
    }
  }

  function clearReindexJob() {
    reindexJob.value = null
    if (!hasActiveJobs.value) {
      stopPolling()
    }
  }

  async function pollAllJobs() {
    // Poll upload jobs (fallback when SSE is not available)
    for (const job of uploadJobs.value) {
      if (job.status === 'pending' || job.status === 'running') {
        // Only poll if no SSE connection
        if (!eventSources.value[job.jobId]) {
          try {
            const response = await axios.get(`/documents/upload/${job.jobId}/status`)
            updateUploadJob(response.data)
          } catch (err) {
            console.error(`Failed to poll upload job ${job.jobId}:`, err)
          }
        }
      }
    }

    // Poll reindex job
    if (reindexJob.value && (reindexJob.value.status === 'pending' || reindexJob.value.status === 'running')) {
      try {
        const response = await axios.get('/api/reindex/status')
        reindexJob.value = response.data
      } catch (err) {
        if (err.response?.status === 404) {
          reindexJob.value = null
        }
      }
    }

    // Stop polling if no more active jobs
    if (!hasActiveJobs.value) {
      stopPolling()
    }
  }

  function startPolling() {
    if (pollingInterval.value) return // Already polling

    pollingInterval.value = setInterval(pollAllJobs, 1500)
  }

  function stopPolling() {
    if (pollingInterval.value) {
      clearInterval(pollingInterval.value)
      pollingInterval.value = null
    }
  }

  // Check for any active jobs on startup
  async function checkActiveJobs() {
    try {
      // Check for active reindex job
      const reindexResponse = await axios.get('/api/reindex/status')
      if (reindexResponse.data && (reindexResponse.data.status === 'pending' || reindexResponse.data.status === 'running')) {
        setReindexJob(reindexResponse.data)
      }
    } catch (err) {
      // No active reindex job
    }

    // Restore active upload jobs (survives page refresh since backend uses threads)
    try {
      const uploadResponse = await axios.get('/documents/upload/active')
      if (uploadResponse.data && uploadResponse.data.length > 0) {
        for (const job of uploadResponse.data) {
          // Only add if not already tracked
          const existing = uploadJobs.value.find(j => j.jobId === job.job_id)
          if (!existing) {
            addUploadJob(job)
          }
        }
      }
    } catch (err) {
      console.error('Failed to check active upload jobs:', err)
    }
  }

  // Cleanup on unmount
  function cleanup() {
    stopPolling()
    closeAllSSEStreams()
  }

  return {
    // State
    uploadJobs,
    reindexJob,

    // Computed
    hasActiveJobs,
    activeJobCount,
    allJobs,

    // Actions
    addUploadJob,
    updateUploadJob,
    removeUploadJob,
    cancelUploadJob,
    setReindexJob,
    clearReindexJob,
    pollAllJobs,
    startPolling,
    stopPolling,
    checkActiveJobs,
    cleanup,
    startSSEStream,
    closeSSEStream,
  }
})
